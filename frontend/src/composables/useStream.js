import { ref } from "vue";
import { useSocket } from "./useSocket";

// Per-udid stream state. The frame is a data URL the DeviceScreen <img> binds.
export function useStream(udid) {
  const { on, emit } = useSocket();
  const frame = ref("");
  const frameWidth = ref(0);
  const frameHeight = ref(0);
  const running = ref(false);
  const provider = ref("");
  const fps = ref(0);
  const error = ref("");

  let lastTs = 0;
  let frameCount = 0;
  const disposers = [];

  function bind() {
    disposers.push(
      on("stream:frame", (d) => {
        if (!d || d.udid !== udid) return;
        frame.value = d.image;
        if (d.width) frameWidth.value = d.width;
        if (d.height) frameHeight.value = d.height;
        // rough fps meter
        frameCount += 1;
        const now = performance.now();
        if (now - lastTs >= 1000) {
          fps.value = frameCount;
          frameCount = 0;
          lastTs = now;
        }
      }),
      on("stream:started", (d) => d?.udid === udid && ((running.value = true), (provider.value = d.provider || ""))),
      on("stream:stopped", (d) => d?.udid === udid && _markStopped()),
      on("stream:status", (d) => {
        if (d?.udid !== udid) return;
        running.value = !!d.running;
        if (d.provider) provider.value = d.provider;
        if (!d.running) _clearFrame();
      }),
      on("stream:error", (d) => {
        if (d && d.udid && d.udid !== udid) return;
        error.value = d?.message || "stream error";
        // The stream is dead — drop the frozen last frame so the screen
        // reflects reality instead of showing a stale image.
        _markStopped();
      }),
    );
  }

  function _clearFrame() {
    frame.value = "";
    fps.value = 0;
  }
  function _markStopped() {
    running.value = false;
    _clearFrame();
  }

  function start(prov, fpsVal) {
    error.value = "";
    running.value = true; // optimistic so the button flips to Stop at once
    emit("stream:start", { udid, provider: prov, fps: fpsVal });
  }
  function stop() {
    // Clear locally right away — don't wait for the server round-trip, so the
    // picture disappears the moment you click Stop.
    _markStopped();
    emit("stream:stop", { udid });
  }
  function dispose() {
    disposers.forEach((off) => off && off());
    disposers.length = 0;
  }

  bind();
  return { frame, frameWidth, frameHeight, running, provider, fps, error, start, stop, dispose };
}
