import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { oss, BUCKET } from './aliyun.mjs'

const DIST = path.resolve(import.meta.dirname, '../dist')
const dryRun = process.argv.includes('--dry-run')

const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
  const p = path.join(dir, d.name)
  return d.isDirectory() ? walk(p) : [p]
})

const cacheControl = (key) => {
  if (key.endsWith('.html')) return 'no-cache'
  if (/^assets\/[^/]+-[\w-]{8}\.(js|css)$/.test(key)) return 'public, max-age=31536000, immutable'
  return 'public, max-age=86400'
}

const local = new Map(walk(DIST).map((f) => [path.relative(DIST, f).split(path.sep).join('/'), f]))

const remote = new Map()
let token
do {
  const r = await oss.listV2({ 'max-keys': 1000, 'continuation-token': token })
  for (const o of r.objects || []) remote.set(o.name, o.etag.replace(/"/g, '').toUpperCase())
  token = r.nextContinuationToken
} while (token)

const md5 = (f) => crypto.createHash('md5').update(fs.readFileSync(f)).digest('hex').toUpperCase()
const toUpload = [...local].filter(([k, f]) => remote.get(k) !== md5(f))
const toDelete = [...remote.keys()].filter((k) => !local.has(k))
// index.html last, so a freshly-deployed page never references assets that haven't landed yet.
toUpload.sort(([a], [b]) => (a === 'index.html') - (b === 'index.html'))

console.log(`bucket ${BUCKET} · local ${local.size} · remote ${remote.size} · upload ${toUpload.length} · delete ${toDelete.length}`)
if (dryRun) process.exit(0)

let done = 0
const queue = toUpload.filter(([k]) => k !== 'index.html')
const worker = async () => {
  for (let item; (item = queue.shift()); ) {
    const [key, file] = item
    await oss.put(key, file, { headers: { 'Cache-Control': cacheControl(key) } })
    if (++done % 50 === 0) console.log(`  ${done}/${toUpload.length}`)
  }
}
await Promise.all(Array.from({ length: 8 }, worker))
const index = toUpload.find(([k]) => k === 'index.html')
if (index) await oss.put(index[0], index[1], { headers: { 'Cache-Control': cacheControl(index[0]) } })

for (let i = 0; i < toDelete.length; i += 1000) await oss.deleteMulti(toDelete.slice(i, i + 1000), { quiet: true })
console.log(`done: uploaded ${toUpload.length}, deleted ${toDelete.length}`)
