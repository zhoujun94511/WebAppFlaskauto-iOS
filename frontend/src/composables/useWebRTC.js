import { ref } from "vue";
import { useSocket } from "./useSocket";

// WebRTC screen transport for one device. Negotiates a recvonly video
// PeerConnection with the backend over Socket.IO (non-trickle: we wait for ICE
// gathering, send one offer, get one answer) and exposes the remote MediaStream
// for a <video> element. Lower latency / higher fps than MJPEG-over-socket.
export function useWebRTC(udid) {
  const { on, emit } = useSocket();
  const stream = ref(null); // MediaStream for the <video>
  const running = ref(false);
  const error = ref("");
  let pc = null;
  let controlChannel = null;
  const disposers = [];
  // Monotonic negotiation token. Bumped on every (re)start/cleanup so an
  // in-flight start() that got superseded by a fast view/device switch won't
  // emit a stale offer (whose answer would later be misapplied to the NEW pc,
  // leaving the video track unnegotiated → black screen while HTTP control
  // still works). Only the latest negotiation is allowed to proceed.
  let negId = 0;

  function _cleanup() {
    negId += 1; // invalidate any in-flight start()
    if (pc) {
      try { pc.close(); } catch { /* ignore */ }
      pc = null;
    }
    controlChannel = null;
    stream.value = null;
    running.value = false;
  }

  // Send a control message (tap/swipe/button/key/text) over the data channel.
  // Returns false if the channel isn't open (caller falls back to HTTP).
  function sendControl(obj) {
    if (controlChannel && controlChannel.readyState === "open") {
      controlChannel.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  async function start() {
    error.value = "";
    _cleanup();
    const myId = negId; // _cleanup() just bumped it; this is our generation
    const myPc = new RTCPeerConnection({ iceServers: [] }); // localhost/LAN: host candidates
    pc = myPc;
    controlChannel = myPc.createDataChannel("control"); // input transport
    myPc.addTransceiver("video", { direction: "recvonly" });
    myPc.ontrack = (ev) => {
      if (pc !== myPc) return;
      stream.value = ev.streams[0] || new MediaStream([ev.track]);
      running.value = true;
    };
    myPc.onconnectionstatechange = () => {
      if (pc === myPc && ["failed", "disconnected", "closed"].includes(myPc.connectionState)) {
        running.value = false;
      }
    };
    const offer = await myPc.createOffer();
    await myPc.setLocalDescription(offer);
    await _waitIceComplete(myPc);
    // Superseded by a newer start()/stop() during ICE gathering → don't emit a
    // stale offer (its answer would corrupt the live pc's media negotiation).
    if (myId !== negId || pc !== myPc) return;
    emit("webrtc:offer", { udid, sdp: myPc.localDescription.sdp, type: myPc.localDescription.type });
  }

  function stop() {
    emit("webrtc:stop", { udid });
    _cleanup();
  }

  function _waitIceComplete(peer) {
    if (peer.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve) => {
      const check = () => {
        if (peer.iceGatheringState === "complete") {
          peer.removeEventListener("icegatheringstatechange", check);
          resolve();
        }
      };
      peer.addEventListener("icegatheringstatechange", check);
      setTimeout(resolve, 2000); // safety: don't hang if it stalls
    });
  }

  function bind() {
    disposers.push(
      on("webrtc:answer", async (d) => {
        if (!pc || d?.udid !== udid) return;
        // Only a peer that has sent an offer and is awaiting the answer can
        // accept one. A late/duplicate answer (after a restart, device switch,
        // or reconnect) arrives when the PC is already 'stable' and would throw
        // "Failed to set remote answer sdp: Called in wrong state: stable".
        if (pc.signalingState !== "have-local-offer") return;
        const target = pc;
        try {
          await target.setRemoteDescription({ sdp: d.sdp, type: d.type });
        } catch (e) {
          if (pc === target) error.value = e.message || "setRemoteDescription failed";
        }
      }),
      on("webrtc:error", (d) => {
        if (d?.udid && d.udid !== udid) return;
        error.value = d?.message || "webrtc error";
        _cleanup();
      }),
    );
  }

  function dispose() {
    disposers.forEach((off) => off && off());
    disposers.length = 0;
    _cleanup();
  }

  bind();
  return { stream, running, error, start, stop, dispose, sendControl };
}
