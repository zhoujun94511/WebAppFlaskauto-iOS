import { ref } from "vue";
import { controlApi } from "../api/control";

// Control actions for one device. tap/swipe send the rendered display size so
// the backend maps to device pixels (handles letterbox + scaling).
export function useControl(udid) {
  const error = ref("");
  const lastScreenshot = ref("");

  // Single-flight for touch input. WDA serializes touch actions on one session
  // (~0.8s each), so firing a tap/swipe on every gesture floods a backlog that
  // shows up as "stuck" pending requests. Drop a new gesture while one is in
  // flight — for remote control the freshest input matters, not a stale queue.
  let touchBusy = false;
  async function _touch(fn) {
    if (touchBusy) return false; // dropped — keeps the channel responsive
    touchBusy = true;
    error.value = "";
    try {
      await fn();
      return true;
    } catch (e) {
      error.value = e.message;
      return false;
    } finally {
      touchBusy = false;
    }
  }

  function tap(x, y, displayWidth, displayHeight) {
    return _touch(() =>
      controlApi.tap(udid, {
        x, y, display_width: displayWidth, display_height: displayHeight,
      }),
    );
  }

  function swipe(x1, y1, x2, y2, displayWidth, displayHeight, duration = 0.3) {
    return _touch(() =>
      controlApi.swipe(udid, {
        x1, y1, x2, y2,
        display_width: displayWidth, display_height: displayHeight, duration,
      }),
    );
  }

  function doubleTap(x, y, displayWidth, displayHeight) {
    return _touch(() =>
      controlApi.doubleTap(udid, {
        x, y, display_width: displayWidth, display_height: displayHeight,
      }),
    );
  }

  function longPress(x, y, displayWidth, displayHeight, duration = 0.8) {
    return _touch(() =>
      controlApi.longPress(udid, {
        x, y, display_width: displayWidth, display_height: displayHeight, duration,
      }),
    );
  }

  async function getAlert() {
    try {
      return await controlApi.getAlert(udid); // { present, text, buttons }
    } catch (e) {
      error.value = e.message;
      return { present: false, text: "", buttons: [] };
    }
  }
  async function alertAction(action) {
    error.value = "";
    try {
      await controlApi.alertAction(udid, action);
    } catch (e) {
      error.value = e.message;
    }
  }

  async function input(text) {
    error.value = "";
    try {
      await controlApi.input(udid, text);
    } catch (e) {
      error.value = e.message;
    }
  }

  // Hardware buttons (home / volumeup / volumedown / lock / unlock). Discrete
  // clicks — no single-flight needed.
  async function pressButton(name) {
    error.value = "";
    try {
      await controlApi.button(udid, name);
    } catch (e) {
      error.value = e.message;
    }
  }

  async function sendKey(name) {
    error.value = "";
    try {
      await controlApi.key(udid, name);
    } catch (e) {
      error.value = e.message;
    }
  }

  async function accessibility(feature, action = "toggle") {
    error.value = "";
    try {
      return await controlApi.accessibility(udid, feature, action);
    } catch (e) {
      error.value = e.message;
    }
  }

  async function pushClipboard(text) {
    error.value = "";
    try {
      await controlApi.setClipboard(udid, text);
      return true;
    } catch (e) {
      error.value = e.message;
      return false;
    }
  }

  async function pullClipboard() {
    error.value = "";
    try {
      return await controlApi.getClipboard(udid); // { text, available }
    } catch (e) {
      error.value = e.message;
      return { text: "", available: false };
    }
  }

  // Screenshots also serialize on WDA; ignore re-clicks while one is loading
  // so mashing the button can't pile up a backlog.
  let shotBusy = false;
  async function screenshot() {
    if (shotBusy) return;
    shotBusy = true;
    error.value = "";
    try {
      const d = await controlApi.screenshot(udid);
      lastScreenshot.value = d.image;
      return d.image;
    } catch (e) {
      error.value = e.message;
    } finally {
      shotBusy = false;
    }
  }

  async function launch(bundleId) {
    error.value = "";
    try {
      await controlApi.launch(udid, bundleId);
    } catch (e) {
      error.value = e.message;
    }
  }

  async function terminate(bundleId) {
    error.value = "";
    try {
      await controlApi.terminate(udid, bundleId);
    } catch (e) {
      error.value = e.message;
    }
  }

  return {
    error, lastScreenshot, tap, swipe, longPress, doubleTap, input, pressButton, sendKey,
    accessibility, getAlert, alertAction, pushClipboard, pullClipboard, screenshot, launch, terminate,
  };
}
