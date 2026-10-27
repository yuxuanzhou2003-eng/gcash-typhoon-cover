import fs from 'node:fs'
import path from 'node:path'
import { ZipArchive } from 'archiver'
import FCClient from '@alicloud/fc2'
import { fcCred, FC_REGION, API_HOST, dnsCall, DOMAIN } from './aliyun.mjs'

const ACCOUNT_ID = process.argv[2]
if (!ACCOUNT_ID) {
  console.error('usage: node scripts/deploy-api-proxy.mjs <aliyun-account-id> [backend-url]')
  process.exit(1)
}
const BACKEND_URL = process.argv[3] || 'https://climada-agent-backend.onrender.com'
const API_KEY = process.env.AGENT_BACKEND_API_KEY
if (!API_KEY) {
  console.error('set AGENT_BACKEND_API_KEY in the environment before running this script')
  process.exit(1)
}

const SERVICE = 'climada'
const FUNC = 'agent-proxy'
const SRC = path.resolve(import.meta.dirname, '../api-proxy')
const ZIP = path.resolve(import.meta.dirname, '../.api-proxy.zip')

const client = new FCClient(ACCOUNT_ID, {
  accessKeyID: fcCred.accessKeyId,
  accessKeySecret: fcCred.accessKeySecret,
  region: FC_REGION,
  timeout: 30000,
})

async function zipSource() {
  await new Promise((resolve, reject) => {
    const out = fs.createWriteStream(ZIP)
    const archive = new ZipArchive()
    out.on('close', resolve)
    archive.on('error', reject)
    archive.pipe(out)
    for (const name of fs.readdirSync(SRC)) archive.file(path.join(SRC, name), { name, mode: 0o644 })
    archive.finalize()
  })
  return fs.readFileSync(ZIP, 'base64')
}

async function ensureService() {
  try {
    await client.getService(SERVICE)
  } catch (e) {
    if (e.code !== 'ServiceNotFound') throw e
    await client.createService(SERVICE, { description: 'CLIMADA agent API proxy' })
  }
}

async function ensureFunction(zipFile) {
  const spec = {
    functionName: FUNC,
    runtime: 'nodejs18',
    handler: 'index.handler',
    memorySize: 128,
    timeout: 30,
    instanceConcurrency: 10,
    environmentVariables: { AGENT_BACKEND_URL: BACKEND_URL, AGENT_BACKEND_API_KEY: API_KEY },
    code: { zipFile },
  }
  try {
    await client.getFunction(SERVICE, FUNC)
    const { code, ...update } = spec
    await client.updateFunction(SERVICE, FUNC, spec)
    console.log('function updated')
  } catch (e) {
    if (e.code !== 'FunctionNotFound') throw e
    await client.createFunction(SERVICE, spec)
    console.log('function created')
  }
}

async function ensureHttpTrigger() {
  const name = 'http-trigger'
  const config = {
    triggerName: name,
    triggerType: 'http',
    triggerConfig: { authType: 'anonymous', methods: ['GET', 'POST', 'OPTIONS'] },
  }
  try {
    await client.getTrigger(SERVICE, FUNC, name)
  } catch (e) {
    if (e.code !== 'TriggerNotFound') throw e
    await client.createTrigger(SERVICE, FUNC, config)
    console.log('http trigger created')
  }
}

async function ensureCustomDomain() {
  const cert = fs.readFileSync(path.resolve(import.meta.dirname, '../.acme/cert.pem'), 'utf8')
  const key = fs.readFileSync(path.resolve(import.meta.dirname, '../.acme/key-rsa.pem'), 'utf8')
  const routeConfig = { routes: [{ path: '/*', serviceName: SERVICE, functionName: FUNC, qualifier: 'LATEST' }] }
  const certConfig = { certName: 'climada-api-cert', certificate: cert, privateKey: key }
  try {
    const existing = await client.getCustomDomain(API_HOST)
    await client.updateCustomDomain(API_HOST, { protocol: 'HTTP,HTTPS', routeConfig, certConfig })
    console.log('custom domain updated')
    return existing.data
  } catch (e) {
    if (e.code !== 'DomainNameNotFound') throw e
    const created = await client.createCustomDomain(API_HOST, { protocol: 'HTTP,HTTPS', routeConfig, certConfig })
    console.log('custom domain created')
    return created.data
  }
}

const zipFile = await zipSource()
await ensureService()
await ensureFunction(zipFile)
await ensureHttpTrigger()
await ensureCustomDomain()
// The DNS CNAME (climada-api -> {accountId}.{region}.fc.aliyuncs.com) was created separately,
// before this domain existed, because FC's DomainNameNotResolved check requires it up front.

fs.unlinkSync(ZIP)
console.log('done')
