import { ref, reactive } from 'vue'

// 讓一個「浮動視窗」可以用標題列拖曳移動。
// 預設維持原本的置中(backdrop flex 置中);使用者一拖曳就切換成自由定位(position: fixed),
// 之後就能拖到畫面任一角落。縮放交給 CSS 的 resize: both。
export function useFloatingPanel() {
  const floating = ref(false)        // 是否已被拖曳成自由定位
  const pos = reactive({ left: 0, top: 0 })
  let startX = 0; let startY = 0; let startLeft = 0; let startTop = 0

  // 傳入拖曳把手(標題列)的 pointerdown 事件;panelSelector 指向要移動的面板
  function onDragStart(e, panelSelector = '.fpanel') {
    if (e.button !== 0) return          // 只處理左鍵
    if (e.target.closest('button, input, select, textarea, a')) return // 點按鈕不算拖曳
    const panel = e.currentTarget.closest(panelSelector)
      || document.querySelector(panelSelector)
    if (!panel) return
    const rect = panel.getBoundingClientRect()
    startLeft = rect.left; startTop = rect.top
    startX = e.clientX; startY = e.clientY
    pos.left = startLeft; pos.top = startTop
    floating.value = true
    window.addEventListener('pointermove', onDragMove)
    window.addEventListener('pointerup', onDragEnd)
    e.preventDefault()
  }
  function onDragMove(e) {
    const nl = startLeft + (e.clientX - startX)
    const nt = startTop + (e.clientY - startY)
    // 不讓標題列被拖出畫面外(至少留 80px 可抓回來)
    pos.left = Math.max(-200, Math.min(window.innerWidth - 80, nl))
    pos.top = Math.max(0, Math.min(window.innerHeight - 40, nt))
  }
  function onDragEnd() {
    window.removeEventListener('pointermove', onDragMove)
    window.removeEventListener('pointerup', onDragEnd)
  }

  // 不經拖曳,直接切換成/取消自由定位(例如「彈出成浮動視窗」按鈕)
  function setFloating(on, left = 140, top = 90) {
    if (on) { pos.left = left; pos.top = top; floating.value = true }
    else { floating.value = false }
  }

  // 綁到面板的 :style —— 自由定位時用 fixed + left/top
  const panelStyle = () => (floating.value
    ? { position: 'fixed', left: pos.left + 'px', top: pos.top + 'px', margin: 0 }
    : {})

  return { floating, onDragStart, panelStyle, setFloating }
}
