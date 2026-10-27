import fs from 'node:fs'
import path from 'node:path'
import dns from 'node:dns/promises'
import acme from 'acme-client'
import { bucketReq, dnsCall, HOSTS, DOMAIN } from './aliyun.mjs'

const DIR = path.resolve(import.meta.dirname, '../.acme')
fs.mkdirSync(DIR, { recursive: true })
const accountFile = path.join(DIR, 'account.pem')
if (!fs.existsSync(accountFile)) fs.writeFileSync(accountFile, await acme.crypto.createPrivateKey())

const client = new acme.Client({
  directoryUrl: acme.directory.letsencrypt.production,
  accountKey: fs.readFileSync(accountFile),
})

const authNs = new dns.Resolver()
authNs.setServers((await Promise.all((await dns.resolveNs(DOMAIN)).map((ns) => dns.resolve4(ns)))).flat())
const waitTxt = async (name, value) => {
  for (let i = 0; i < 30; i++) {
    const got = await authNs.resolveTxt(name).catch(() => [])
    if (got.flat().includes(value)) return
    await new Promise((r) => setTimeout(r, 5000))
  }
  throw new Error(`TXT ${name} not visible`)
}

const added = []
const [key, csr] = await acme.crypto.createCsr({ commonName: HOSTS[0], altNames: HOSTS })
const cert = await client.auto({
  csr,
  email: 'yuxuan.zhou2003@gmail.com',
  termsOfServiceAgreed: true,
  challengePriority: ['dns-01'],
  challengeCreateFn: async (authz, _challenge, keyAuth) => {
    const host = authz.identifier.value
    const rr = '_acme-challenge' + (host === DOMAIN ? '' : '.' + host.replace('.' + DOMAIN, ''))
    const r = await dnsCall('AddDomainRecord', { DomainName: DOMAIN, RR: rr, Type: 'TXT', Value: keyAuth, TTL: 600 })
    added.push(r.RecordId)
    await waitTxt(`${rr}.${DOMAIN}`, keyAuth)
  },
  challengeRemoveFn: async () => {},
})
for (const id of added) await dnsCall('DeleteDomainRecord', { RecordId: id })

fs.writeFileSync(path.join(DIR, 'cert.pem'), cert)
fs.writeFileSync(path.join(DIR, 'key.pem'), key)

// Only the OSS-hosted site host gets the OSS CNAME certificate; the API host's TLS is handled
// by Function Compute's own custom-domain certificate (see deploy-api-proxy.mjs).
try {
  await bucketReq('POST', { cname: '', comp: 'add' }, `<?xml version="1.0" encoding="UTF-8"?><BucketCnameConfiguration><Cname><Domain>${HOSTS[0]}</Domain><CertificateConfiguration><Certificate>${cert}</Certificate><PrivateKey>${key}</PrivateKey><Force>true</Force></CertificateConfiguration></Cname></BucketCnameConfiguration>`)
  console.log('cert installed', HOSTS[0])
} catch (e) {
  console.log('cert install failed:', e.code || e.name, '-', e.message)
  console.log('cert.pem / key.pem saved under .acme/; rerun once the required RAM permission is granted.')
}

const info = acme.crypto.readCertificateInfo(cert)
console.log('valid until', info.notAfter.toISOString().slice(0, 10))
