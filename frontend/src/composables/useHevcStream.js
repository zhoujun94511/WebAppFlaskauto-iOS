import { ref } from "vue";
import { useSocket } from "./useSocket";

// Native HEVC path. The phone already encoded the picture; the browser
// decodes it with WebCodecs. This does not go through the WebRTC encoder.
// iOS 18.6.2 answers startmediastream with code 9021: remote screen
// requires iOS 27 or later. Older phones stay on MJPEG/WebRTC.
export function iosSupportsHevc(version) {
  const major = parseInt(String(version || "").split(".")[0], 10);
  return Number.isFinite(major) && major >= 27;
}

// L150 is the codec string measured from iPhone17,5 / iOS 27.0. L93 is the
// lower level some browsers advertise first. Either one is enough to try;
// the real hvcC is checked again in applyConfig.
const HEVC_PROBE_CODECS = ["hev1.1.6.L150.B0", "hev1.1.6.L93.B0"];

export async function browserSupportsHevc() {
  if (typeof VideoDecoder === "undefined" || !VideoDecoder.isConfigSupported) return false;
  for (const codec of HEVC_PROBE_CODECS) {
    try {
      const res = await VideoDecoder.isConfigSupported({ codec });
      if (res.supported) return true;
    } catch {
      /* this level is unsupported; try the next one */
    }
  }
  return false;
}

function toU8(data) {
  if (!data) return null;
  if (data instanceof Uint8Array) return data;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  return null;
}

function b64ToU8(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
}

export function useHevcStream(udid) {
  const { on, emit } = useSocket();
  const running = ref(false);
  const error = ref("");
  let canvas = null;
  let ctx = null;
  let decoder = null;
  let decoderConfig = null;
  let timestamp = 0;
  let gotKey = false;
  let needsResync = false;
  let handedOff = false;
  let active = false;
  let configuring = false;
  let pendingPackets = [];
  let keyAskedAt = 0;
  let onFallback = () => {};
  const disposers = [];

  function setFallback(fn) {
    onFallback = fn || (() => {});
  }

  function bind(el) {
    if (el && el.tagName !== "CANVAS") return;
    canvas = el;
    ctx = el ? el.getContext("2d") : null;
  }

  function closeDecoder() {
    try { decoder?.close(); } catch { /* already closed */ }
    decoder = null;
    gotKey = false;
    needsResync = false;
  }

  function buildDecoder() {
    return new VideoDecoder({
      output: (frame) => {
        if (ctx && canvas) {
          if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
            canvas.width = frame.displayWidth;
            canvas.height = frame.displayHeight;
          }
          ctx.drawImage(frame, 0, 0);
          running.value = true;
        }
        frame.close();
      },
      error: () => {
        needsResync = true;
        emit("stream:keyframe", { udid });
      },
    });
  }

  async function applyConfig(codec, description) {
    decoderConfig = { codec, description, optimizeForLatency: true };
    let supported = false;
    try {
      const res = await VideoDecoder.isConfigSupported(decoderConfig);
      supported = !!res.supported;
    } catch {
      supported = false;
    }
    if (!supported) {
      configuring = false;
      pendingPackets = [];
      error.value = "浏览器无法解码这路 HEVC";
      handOff("hevc");
      return;
    }
    try {
      closeDecoder();
      decoder = buildDecoder();
      decoder.configure(decoderConfig);
    } catch {
      configuring = false;
      pendingPackets = [];
      handOff("hevc");
      return;
    }
    gotKey = false;
    needsResync = false;
    configuring = false;
    const queued = pendingPackets;
    pendingPackets = [];
    queued.forEach((bytes) => decodePacket(bytes));
  }

  function noteGap() {
    needsResync = true;
    const now = performance.now();
    if (now - keyAskedAt < 500) return;
    keyAskedAt = now;
    emit("stream:keyframe", { udid });
  }

  function decodePacket(bytes) {
    if (!decoder || !decoderConfig || bytes.length < 5) return;
    const len = (bytes[0] << 24) | (bytes[1] << 16) | (bytes[2] << 8) | bytes[3];
    if (bytes.length < 4 + len) return;
    const type = bytes[4];
    const data = bytes.slice(5, 4 + len);
    // A backed-up decoder is the same gap as a dropped packet: skip the late
    // delta and wait for a keyframe instead of painting further and further behind.
    if (type === 1 && decoder.decodeQueueSize > 4) {
      noteGap();
      return;
    }
    if (type === 2 || (type === 0 && needsResync)) {
      closeDecoder();
      decoder = buildDecoder();
      decoder.configure(decoderConfig);
      needsResync = false;
      gotKey = true;
    } else if (type === 0) {
      gotKey = true;
    }
    if (!gotKey || needsResync || decoder.state !== "configured") return;
    try {
      decoder.decode(new EncodedVideoChunk({
        type: type === 1 ? "delta" : "key",
        timestamp,
        data,
      }));
      timestamp += 16666;
    } catch {
      needsResync = true;
      emit("stream:keyframe", { udid });
    }
  }

  function handOff(serverProvider) {
    if (handedOff || !active) return;
    handedOff = true;
    active = false;
    closeDecoder();
    running.value = false;
    onFallback(serverProvider || "");
  }

  function bindSocket() {
    disposers.push(
      on("stream:hevc-config", (d) => {
        if (!active || !d || d.udid !== udid || !d.codec || !d.description) return;
        configuring = true;
        applyConfig(d.codec, b64ToU8(d.description));
      }),
      on("stream:hevc", (d) => {
        if (!active || !d || d.udid !== udid) return;
        const bytes = toU8(d.packet);
        if (!bytes) return;
        // Config check is async. Hold frames, including the first key, until
        // VideoDecoder.configure has finished.
        if (!decoder || configuring) {
          pendingPackets.push(bytes);
          if (pendingPackets.length > 45) {
            pendingPackets.shift();
            noteGap();
          }
          return;
        }
        decodePacket(bytes);
      }),
      on("stream:started", (d) => {
        if (!active || !d || d.udid !== udid) return;
        if (d.provider && d.provider !== "hevc") handOff(d.provider);
      }),
      on("stream:error", (d) => {
        if (!active) return;
        if (d && d.udid && d.udid !== udid) return;
        error.value = d?.message || "HEVC stream failed";
        // The server first retries MJPEG. Wait for that stream:started; if it
        // never comes, leave the HEVC view ourselves.
        setTimeout(() => {
          if (active) handOff("hevc");
        }, 1600);
      }),
    );
  }

  function start() {
    error.value = "";
    handedOff = false;
    active = true;
    running.value = false;
    emit("stream:start", { udid, provider: "auto" });
  }

  function stop() {
    active = false;
    closeDecoder();
    running.value = false;
    emit("stream:stop", { udid });
  }

  function dispose() {
    active = false;
    closeDecoder();
    disposers.forEach((off) => off && off());
    disposers.length = 0;
  }

  bindSocket();
  return { running, error, bind, setFallback, start, stop, dispose };
}
