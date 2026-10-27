import fs from 'node:fs'
import OSS from 'ali-oss'
import Core from '@alicloud/pop-core'

const env = Object.fromEntries(
  fs.readFileSync(new URL('../.env', import.meta.url), 'utf8')
    .split(/\r?\n/).filter((l) => /^\w+=/.test(l))
    .map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1).trim()]),
)
const cred = { accessKeyId: env.ALIYUN_ACCESS_KEY_ID, accessKeySecret: env.ALIYUN_ACCESS_KEY_SECRET }

export const BUCKET = 'climada-ezethan'
export const REGION = 'oss-cn-hongkong'
export const DOMAIN = 'ezethan.com'
export const SITE_HOST = 'climada.ezethan.com'
export const API_HOST = 'climada-api.ezethan.com'
export const HOSTS = [SITE_HOST, API_HOST]

export const oss = new OSS({ ...cred, region: REGION, bucket: BUCKET, secure: true })
export const dns = new Core({ ...cred, endpoint: 'https://alidns.aliyuncs.com', apiVersion: '2015-01-09' })
export const dnsCall = (action, params) => dns.request(action, params, { method: 'POST' })

export async function bucketReq(method, subres, xml) {
  return oss.request({
    method, bucket: BUCKET, subres,
    content: xml, headers: xml ? { 'Content-Type': 'application/xml' } : {},
    successStatuses: [200],
  })
}

export const FC_REGION = 'cn-hongkong'
export const fcCred = cred
