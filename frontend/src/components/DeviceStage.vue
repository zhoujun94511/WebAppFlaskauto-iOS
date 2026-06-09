<template>
  <article v-if="!compact" ref="cardEl" class="device-card" :class="{ live }" :style="{ '--card-aspect': aspect }">
    <header class="device-card-head">
      <span class="device-card-title">{{ deviceName }}</span>
      <span class="device-card-chip" :class="live ? 'on' : 'off'">
        <span class="dot" />{{ chipText }}
      </span>
    </header>

    <DeviceScreen
      :udid="udid"
      :frame="useRtc ? '' : stream.frame.value"
      :stream="useRtc ? rtc.stream.value : null"
      :rtc-control="useRtc ? rtc.sendControl : null"
    />

    <footer class="device-card-actions">
      <button class="card-action" :title="t('control.home')" @click="ctrl.pressButton('home')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12l9-9 9 9" /><path d="M5 10v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V10" /></svg>
      </button>
      <button class="card-action" :title="t('control.lock')" @click="ctrl.pressButton('lock')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
      </button>
      <!-- D-pad — summoned as a floating pad above the footer (mirrors Android's popover navpad) -->
      <button class="card-action" :class="{ active: dpadOpen }" :title="t('quick.dpad')" @click="dpadOpen = !dpadOpen">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4" /><path d="m9 6 3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3" /></svg>
      </button>
      <span class="card-action-spacer" />
      <button class="card-action" :title="t('control.volUp')" @click="ctrl.pressButton('volumeup')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4z" /><path d="M16 9a4 4 0 0 1 0 6" /><path d="M19.5 6.5a8 8 0 0 1 0 11" /></svg>
      </button>
      <button class="card-action" :title="t('control.volDown')" @click="ctrl.pressButton('volumedown')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4z" /><path d="M16 9a4 4 0 0 1 0 6" /></svg>
      </button>
      <!-- "更多" — reveals the right-side control drawer (control/automation/apps/files/logs) -->
      <button class="card-action" :class="{ active: moreOpen }" :title="t('control.more')" @click="emit('toggle-more')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="8" x2="20" y2="8" /><line x1="4" y1="16" x2="20" y2="16" /><circle cx="9" cy="8" r="2" fill="currentColor" stroke="none" /><circle cx="15" cy="16" r="2" fill="currentColor" stroke="none" /></svg>
      </button>
    </footer>

    <p v-if="rtc.error.value || stream.error.value" class="banner err" style="margin: 0; border-radius: 0">
      {{ rtc.error.value || stream.error.value }}
    </p>

    <!-- MJPEG fallback (Socket.IO) provider/fps knobs — only when WebRTC is off -->
    <StreamPanel
      v-if="!useRtc"
      :running="stream.running.value"
      :connected="connected"
      :active-provider="stream.provider.value"
      :live-fps="stream.fps.value"
      :error="stream.error.value"
      @start="(o) => stream.start(o.provider, o.fps)"
      @stop="stream.stop()"
    />
  </article>

  <!-- compact (grid tile): bare screen only -->
  <DeviceScreen
    v-else
    :udid="udid"
    :frame="useRtc ? '' : stream.frame.value"
    :stream="useRtc ? rtc.stream.value : null"
    :rtc-control="useRtc ? rtc.sendControl : null"
  />

  <!-- Floating D-pad — summoned from the footer navpad button. Teleported to
       <body> and anchored to the card's outer-right edge (Android-style relative
       positioning) so it never covers the screen. Center is a visual hub only. -->
  <Teleport to="body">
    <transition name="dpad-pop">
      <div v-if="dpadOpen" ref="dpadEl" class="dpad-pop" :style="dpadStyle" role="group" :aria-label="t('quick.dpad')">
        <button class="dpad-close" :title="t('detail.close')" :aria-label="t('detail.close')" @click="dpadOpen = false">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18" /><line x1="18" y1="6" x2="6" y2="18" /></svg>
        </button>
        <button class="dpad-key up" :title="t('quick.up')" @click="dir('up')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 11 12 5 18 11" /><line x1="12" y1="19" x2="12" y2="5" /></svg>
        </button>
        <button class="dpad-key left" :title="t('quick.left')" @click="dir('left')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="11 6 5 12 11 18" /><line x1="19" y1="12" x2="5" y2="12" /></svg>
        </button>
        <span class="dpad-hub" aria-hidden="true" />
        <button class="dpad-key right" :title="t('quick.right')" @click="dir('right')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="13 6 19 12 13 18" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
        </button>
        <button class="dpad-key down" :title="t('quick.down')" @click="dir('down')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 13 12 19 18 13" /><line x1="12" y1="5" x2="12" y2="19" /></svg>
        </button>
      </div>
    </transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import DeviceScreen from "./DeviceScreen.vue";
import StreamPanel from "./StreamPanel.vue";
import { useStream } from "../composables/useStream";
import { useWebRTC } from "../composables/useWebRTC";
import { useDevices } from "../composables/useDevices";
import { useControl } from "../composables/useControl";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  udid: { type: String, required: true },
  connected: { type: Boolean, default: false },
  compact: { type: Boolean, default: false }, // grid tile: screen only, no chrome
  moreOpen: { type: Boolean, default: false }, // highlight footer "更多" when drawer is open
});
const emit = defineEmits(["toggle-more"]);

const { devices, webrtcEnabled } = useDevices();
const useRtc = webrtcEnabled; // reactive ref; chosen once health is known

const stream = useStream(props.udid);
const rtc = useWebRTC(props.udid);
const ctrl = useControl(props.udid);

const device = computed(() => devices.value.find((d) => d.udid === props.udid));
// Prefer the marketing name ("iPhone 15 Pro Max") over the lockdown DeviceName
// (typically just "iPhone" if the user never renamed the device). Backend stamps
// `marketing` onto every device in /api/devices via IOSDevice.to_dict().
const deviceName = computed(() => device.value?.marketing || device.value?.name || props.udid.slice(0, 14));
// Card hugs the device aspect ratio (w/h); fall back to a modern-phone portrait.
const aspect = computed(() => {
  const w = device.value?.screen_width, h = device.value?.screen_height;
  return w && h ? +(w / h).toFixed(4) : 0.49;
});
// Floating D-pad (footer-summoned). iOS has no hardware arrows, so directions
// are WDA swipes from screen-center on a normalized 1000×1000 box (the backend
// maps proportionally to device pixels). There is no center action — the hub is
// a visual anchor only (mirrors Android's `dpad-hub`).
const dpadOpen = ref(false);
const dpadEl = ref(null);
const cardEl = ref(null);
const dpadStyle = ref({});
const B = 1000, C = 500, NEAR = 320, FAR = 680;
function dir(d) {
  const m = {
    up: [C, FAR, C, NEAR],
    down: [C, NEAR, C, FAR],
    left: [FAR, C, NEAR, C],
    right: [NEAR, C, FAR, C],
  }[d];
  if (m) ctrl.swipe(m[0], m[1], m[2], m[3], B, B, 0.18);
}

// Anchor the D-pad to the card's OUTER-right edge (fall back to outer-left, then
// to floating over the lower-right corner on viewports too narrow for either),
// vertically aligned to the lower third of the card so it sits within thumb
// reach. Re-runs on resize/scroll + a ResizeObserver on the stage so it tracks
// the card when the control drawer pushes the layout. Mirrors Android's JS
// relative-positioning (CSS Anchor Positioning isn't in Safari/FF yet).
const GAP = 12, PAD_W = 132, MARGIN = 8;
function positionDpad() {
  const card = cardEl.value;
  if (!dpadOpen.value || !card) return;
  const r = card.getBoundingClientRect();
  const pad = dpadEl.value?.getBoundingClientRect();
  const w = pad?.width || PAD_W;
  const h = pad?.height || PAD_W;
  const vw = window.innerWidth, vh = window.innerHeight;

  let left;
  if (r.right + GAP + w <= vw - MARGIN) left = r.right + GAP;        // right of card
  else if (r.left - GAP - w >= MARGIN) left = r.left - GAP - w;       // left of card
  else left = Math.min(r.right - w - GAP, vw - w - MARGIN);           // overlay lower-right

  // Sit in the card's lower third (thumb reach), clamped to the viewport.
  let top = r.top + r.height * 0.62 - h / 2;
  top = Math.max(MARGIN, Math.min(top, vh - h - MARGIN));
  left = Math.max(MARGIN, Math.min(left, vw - w - MARGIN));
  dpadStyle.value = { top: `${Math.round(top)}px`, left: `${Math.round(left)}px` };
}

let _raf = 0;
function scheduleReposition() {
  if (_raf) return;
  _raf = requestAnimationFrame(() => { _raf = 0; positionDpad(); });
}
let _ro = null;
function bindRepositionListeners() {
  window.addEventListener("resize", scheduleReposition);
  window.addEventListener("scroll", scheduleReposition, true);
  if (cardEl.value && "ResizeObserver" in window) {
    _ro = new ResizeObserver(scheduleReposition);
    // Observe the stage cell (parent) so a drawer-induced layout shift retracks.
    _ro.observe(cardEl.value);
    if (cardEl.value.parentElement) _ro.observe(cardEl.value.parentElement);
  }
}
function unbindRepositionListeners() {
  window.removeEventListener("resize", scheduleReposition);
  window.removeEventListener("scroll", scheduleReposition, true);
  _ro?.disconnect();
  _ro = null;
}
watch(dpadOpen, async (open) => {
  if (open) {
    await nextTick();
    positionDpad();
    positionDpad(); // 2nd pass now that the element has a measured size
    bindRepositionListeners();
  } else {
    unbindRepositionListeners();
  }
});

const live = computed(() => (useRtc.value ? rtc.running.value : stream.running.value));
const chipText = computed(() =>
  useRtc.value
    ? (rtc.running.value ? t("stream.live") + " · WebRTC" : t("stream.connecting"))
    : (stream.running.value ? t("stream.live") : t("stream.connecting")),
);

// Once connected (WDA up), start whichever transport is enabled.
watch(
  () => props.connected,
  (isConnected) => {
    if (!isConnected) return;
    if (useRtc.value) {
      rtc.start();
    } else if (!stream.running.value) {
      stream.start();
    }
  },
  { immediate: true },
);

onUnmounted(() => {
  stream.stop();
  stream.dispose();
  rtc.stop();
  rtc.dispose();
  unbindRepositionListeners();
});
</script>

<style scoped>
/* Floating D-pad — fixed-positioned (coords set in JS), anchored beside the card
   so it never covers the screen. No center action: the hub is a visual anchor. */
.dpad-pop {
  position: fixed; z-index: 38;
  display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: repeat(3, 1fr);
  gap: 6px; width: 132px; aspect-ratio: 1; padding: 8px;
  background: var(--panel-solid); border: 1px solid var(--border-strong);
  border-radius: 16px; box-shadow: var(--shadow); backdrop-filter: blur(10px);
}
.dpad-key { min-height: 0; height: 100%; padding: 0; display: flex; align-items: center; justify-content: center; }
.dpad-key svg { width: 18px; height: 18px; }
.dpad-close {
  grid-area: 1 / 3; align-self: start; justify-self: end;
  min-height: 0; width: 22px; height: 22px; padding: 0; border-radius: 7px;
  display: flex; align-items: center; justify-content: center; color: var(--muted);
}
.dpad-close svg { width: 13px; height: 13px; }
.dpad-close:hover { color: var(--text-strong); }
.dpad-key.up { grid-area: 1 / 2; }
.dpad-key.left { grid-area: 2 / 1; }
.dpad-key.right { grid-area: 2 / 3; }
.dpad-key.down { grid-area: 3 / 2; }
.dpad-hub { grid-area: 2 / 2; align-self: center; justify-self: center; width: 12px; height: 12px; border-radius: 50%; background: var(--border-strong); }
.dpad-pop-enter-active, .dpad-pop-leave-active { transition: opacity 0.16s ease, transform 0.16s ease; }
.dpad-pop-enter-from, .dpad-pop-leave-to { opacity: 0; transform: scale(0.92); }
</style>
