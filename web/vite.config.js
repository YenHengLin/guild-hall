import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // host:true → 綁 0.0.0.0,讓 Tailscale 網路上的其他裝置(手機/筆電)連得進來。
    // 後端仍留在 127.0.0.1,只透過下面的 proxy 轉發 —— 不直接暴露在 tailnet 上。
    host: true,
    port: 5173,
    // Vite 會擋掉不認識的主機名(防 DNS rebinding)。放行 Tailscale MagicDNS 的
    // *.ts.net 主機名;若改用 100.x.x.x 這種 IP 連,Vite 本來就會放行,不受此限。
    allowedHosts: ['.ts.net'],
    proxy: {
      '/api': 'http://127.0.0.1:8848',
      '/uploads': 'http://127.0.0.1:8848',
    },
  },
})
