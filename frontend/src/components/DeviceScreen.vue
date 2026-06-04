<template>
  <div
    class="screen-wrap"
    @pointerdown.prevent="onDown"
    @pointerup.prevent="onUp"
    @pointercancel="onCancel"
    @dblclick.prevent="onDblClick"
  >
    <video
      v-if="stream"
      ref="mediaRef"
      class="screen-img"
      autoplay
      playsinline
      muted
      draggable="false"
    />
    <img
      v-else-if="frame"
      ref="mediaRef"
      class="screen-img"
      :src="frame"
      draggable="false"
    />
    <div v-else class="screen-empty muted">
      <img class="screen-empty-glyph" src="/placeholder-mobile.svg" alt="" />
      <span>{{ t("stream.noFrame") }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from "vue";
import { useControl } from "../composables/useControl";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  udid: { type: String, required: true },
  frame: { type: String, default: "" },
  stream: { type: Object, default: null }, // MediaStream (WebRTC) or null
  rtcControl: { type: Function, default: null }, // send input over the RTC data channel
});

const { tap, swipe, longPress, doubleTap } = useControl(props.udid);
const mediaRef = ref(null);

// Bind the WebRTC MediaStream to the <video> whenever it (re)appears.
watch(
  () => props.stream,
  async (s) => {
    await nextTick();
    const el = mediaRef.value;
    if (!el || !("srcObject" in el)) return;
    el.srcObject = s || null;
    // Setting srcObject programmatically (after the <video> mounts) doesn't
    // always trigger `autoplay` — the element can stay paused → black even
    // though the track is live. Kick playback explicitly; the promise rejects
    // harmlessly if a newer srcObject supersedes this one mid device-switch.
    if (s) el.play?.().catch(() => {});
  },
  { immediate: true },
);

const TAP_MAX_DIST = 10;
const TAP_MAX_MS = 400;
const LONG_PRESS_MS = 500; // hold in place longer than this → long-press
let start = null;

function point(ev) {
  const rect = mediaRef.value.getBoundingClientRect();
  return { x: ev.clientX - rect.left, y: ev.clientY - rect.top, w: rect.width, h: rect.height };
}

function onDown(ev) {
  if (!mediaRef.value) return;
  start = { ...point(ev), t: performance.now() };
  mediaRef.value.setPointerCapture?.(ev.pointerId);
}

function onUp(ev) {
  if (!start || !mediaRef.value) return;
  const p = point(ev);
  const dist = Math.hypot(p.x - start.x, p.y - start.y);
  const dt = performance.now() - start.t;
  if (dist <= TAP_MAX_DIST && dt <= TAP_MAX_MS) {
    const msg = { type: "tap", x: Math.round(p.x), y: Math.round(p.y), display_width: p.w, display_height: p.h };
    if (!(props.rtcControl && props.rtcControl(msg))) tap(msg.x, msg.y, p.w, p.h);
  } else if (dist <= TAP_MAX_DIST && dt >= LONG_PRESS_MS) {
    const dur = Math.min(3.0, dt / 1000);
    const msg = { type: "longpress", x: Math.round(p.x), y: Math.round(p.y), display_width: p.w, display_height: p.h, duration: dur };
    if (!(props.rtcControl && props.rtcControl(msg))) longPress(msg.x, msg.y, p.w, p.h, dur);
  } else {
    const dur = Math.min(1.0, Math.max(0.1, dt / 1000));
    const msg = {
      type: "swipe", x1: Math.round(start.x), y1: Math.round(start.y),
      x2: Math.round(p.x), y2: Math.round(p.y),
      display_width: p.w, display_height: p.h, duration: dur,
    };
    if (!(props.rtcControl && props.rtcControl(msg)))
      swipe(msg.x1, msg.y1, msg.x2, msg.y2, p.w, p.h, dur);
  }
  start = null;
}

function onCancel() {
  start = null;
}

function onDblClick(ev) {
  if (!mediaRef.value) return;
  const p = point(ev);
  const msg = { type: "doubletap", x: Math.round(p.x), y: Math.round(p.y), display_width: p.w, display_height: p.h };
  if (!(props.rtcControl && props.rtcControl(msg))) doubleTap(msg.x, msg.y, p.w, p.h);
}
</script>

<style scoped>
.screen-wrap {
  display: flex;
  justify-content: center;
  align-items: center;
  background: radial-gradient(120% 80% at 50% 0%, #0e1626, #05080f);
  border: 1px solid var(--border);
  border-radius: 18px;
  overflow: hidden;
  min-height: 260px;
  padding: 10px;
  box-shadow: var(--shadow), inset 0 0 0 1px rgba(255, 255, 255, 0.03);
}
.screen-img {
  max-width: 100%;
  /* Cap to the viewport so the whole card (header + screen + footer) stays in
     view without page-scrolling. On tall screens 78vh wins; on shorter ones the
     calc shrinks the screen just enough to keep the footer controls on-screen.
     ~300px covers the topbar + page-head + device strip + card chrome above. */
  max-height: min(78vh, calc(100vh - 348px));
  display: block;
  border-radius: 8px;
  touch-action: none;
  user-select: none;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5);
}
.screen-empty {
  padding: 60px 20px;
  text-align: center;
  font-size: 0.86rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}
.screen-empty-glyph {
  width: 64px;
  height: auto;
  opacity: 0.7;
}
</style>
