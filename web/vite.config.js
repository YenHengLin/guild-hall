import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import fs from 'node:fs'
import net from 'node:net'
import { fileURLToPath } from 'node:url'
import { parse } from 'yaml'

// 讀專案根目錄的 config.yaml(與後端同一份;優先序:環境變數 > config.yaml > 預設)
function loadConfig() {
  const path = process.env.GUILD_CONFIG
    || fileURLToPath(new URL('../config.yaml', import.meta.url))
  try {
    return parse(fs.readFileSync(path, 'utf8')) || {}
  } catch {
    return {} // 沒有設定檔 → 全用預設
  }
}
const cfg = loadConfig()
const envFlag = process.env.GUILD_TAILSCALE
const tailscale = envFlag !== undefined ? envFlag === '1' : cfg.tailscale === true
const tsHostname = (process.env.GUILD_TAILSCALE_HOSTNAME ?? cfg.tailscale_hostname ?? '').trim()

// Tailscale 位址段:IPv4 100.64.0.0/10、IPv6 fd7a:115c:a1e0::/48
const tsBlocks = new net.BlockList()
tsBlocks.addSubnet('100.64.0.0', 10, 'ipv4')
tsBlocks.addSubnet('fd7a:115c:a1e0::', 48, 'ipv6')

function allowedRemote(addr = '') {
  const ip = addr.startsWith('::ffff:') ? addr.slice(7) : addr
  if (ip === '127.0.0.1' || ip === '::1') return true
  const type = net.isIPv4(ip) ? 'ipv4' : net.isIPv6(ip) ? 'ipv6' : null
  return !!type && tsBlocks.check(ip, type)
}

// 開了 tailscale 時必須綁 0.0.0.0(Vite 只能綁一個位址),所以在最前面擋:
// 只放行本機與 tailnet 來的連線,同網段 / ZeroTier / 公用網路的人一律 403。
// (API 沒有登入驗證,且派遣的冒險者能跑 Bash —— 絕不能讓外人連到)
const tailnetGuard = {
  name: 'guild-tailnet-guard',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if (allowedRemote(req.socket.remoteAddress)) return next()
      res.statusCode = 403
      res.end('Forbidden: only localhost and Tailscale clients are allowed')
    })
    server.httpServer?.on('upgrade', (req, socket) => {
      if (!allowedRemote(req.socket.remoteAddress)) socket.destroy()
    })
    server.httpServer?.once('listening', () => {
      const port = server.config.server.port
      const where = tsHostname ? `http://${tsHostname}:${port}` : `http://<你的 Tailscale IP>:${port}`
      server.config.logger.info(`  ➜  Tailscale: ${where}(只放行本機與 tailnet)`)
    })
  },
}

export default defineConfig({
  plugins: [vue(), ...(tailscale ? [tailnetGuard] : [])],
  server: {
    // 預設只聽本機。config.yaml 設 tailscale: true 才對外開放(且只放行 tailnet,見上方守門)。
    // 後端一律留在 127.0.0.1,只透過下面的 proxy 轉發。
    host: tailscale ? '0.0.0.0' : '127.0.0.1',
    port: 5173,
    // Vite 會擋不認識的主機名(防 DNS rebinding)。放行 MagicDNS 的 *.ts.net;
    // 用 100.x.x.x IP 連時 Vite 本來就放行。
    allowedHosts: tailscale ? ['.ts.net', ...(tsHostname ? [tsHostname] : [])] : [],
    proxy: {
      '/api': 'http://127.0.0.1:8848',
      '/uploads': 'http://127.0.0.1:8848',
    },
  },
})
