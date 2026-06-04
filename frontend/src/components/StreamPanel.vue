<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <strong>{{ t("stream.title") }}</strong>
      <span class="pill" :class="running ? 'on' : 'off'">
        {{ running ? t("stream.live") : t("stream.stop") }}
      </span>
    </div>

    <div class="row" style="margin-top: 10px; flex-wrap: wrap">
      <label class="muted">{{ t("stream.provider") }}</label>
      <select v-model="provider">
        <option value="mjpeg">{{ t("stream.providerMjpeg") }}</option>
        <option value="screenshot">{{ t("stream.providerScreenshot") }}</option>
      </select>
      <button
        v-if="!running"
        class="primary"
        :disabled="!connected"
        :title="connected ? '' : t('stream.connectFirst')"
        @click="emitStart"
      >
        {{ t("stream.start") }}
      </button>
      <button v-else class="danger" @click="$emit('stop')">{{ t("stream.stop") }}</button>
    </div>

    <!-- FPS control: drives the screenshot poll rate (and caps any provider). -->
    <div class="row" style="margin-top: 10px; flex-wrap: wrap">
      <label class="muted">{{ t("stream.fps") }}</label>
      <input
        type="range"
        min="1"
        max="30"
        step="1"
        v-model.number="fps"
        style="flex: 1 1 120px"
      />
      <input
        type="number"
        min="1"
        max="30"
        v-model.number="fps"
        style="width: 64px"
      />
      <button :disabled="!running" @click="emitStart">
        {{ t("stream.apply") }}
      </button>
    </div>

    <div class="muted" style="margin-top: 8px; font-size: 0.78rem">
      {{ t("stream.stats", { provider: activeProvider || "—", target: fps, live: liveFps }) }}
    </div>
    <p v-if="error" class="banner err" style="margin-top: 8px">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  running: Boolean,
  connected: { type: Boolean, default: false },
  activeProvider: { type: String, default: "" },
  liveFps: { type: Number, default: 0 },
  error: { type: String, default: "" },
  initialFps: { type: Number, default: 8 },
});
const emit = defineEmits(["start", "stop"]);

const provider = ref("mjpeg");
const fps = ref(props.initialFps);

function emitStart() {
  const v = Math.max(1, Math.min(30, Number(fps.value) || 8));
  fps.value = v;
  emit("start", { provider: provider.value, fps: v });
}
</script>
