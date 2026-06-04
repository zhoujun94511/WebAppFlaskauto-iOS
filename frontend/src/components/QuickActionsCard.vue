<template>
  <div class="card">
    <strong>{{ t("quick.title") }}</strong>

    <!-- Accessibility software-button toggles via go-ios.
         (Directional D-pad lives in the device-card footer — summoned there.) -->
    <label class="muted" style="font-size: 0.72rem; display: block; margin-top: 10px">{{ t("quick.accessibility") }}</label>
    <div class="row" style="flex-wrap: wrap; gap: 6px; margin-top: 4px">
      <button @click="toggleA11y('assistivetouch')">{{ t("quick.assistiveTouch") }}</button>
      <button @click="toggleA11y('voiceover')">{{ t("quick.voiceOver") }}</button>
      <button @click="toggleA11y('zoom')">{{ t("quick.zoom") }}</button>
    </div>
  </div>
</template>

<script setup>
import { useControl } from "../composables/useControl";
import { useUiI18n } from "../composables/useUiI18n";

import { useToast } from "../composables/useToast";

const props = defineProps({ udid: { type: String, required: true } });
const { t } = useUiI18n();
const ctrl = useControl(props.udid);
const { push: toast } = useToast();

const A11Y_LABEL = {
  assistivetouch: "quick.assistiveTouch",
  voiceover: "quick.voiceOver",
  zoom: "quick.zoom",
};
async function toggleA11y(feature) {
  const r = await ctrl.accessibility(feature, "toggle");
  if (r) toast(`${t(A11Y_LABEL[feature])} · ${r.enabled ? t("quick.on") : t("quick.off")}`);
}
</script>
