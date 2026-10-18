/* Offline camera scanning. No inventory changes happen in this module. */
(() => {
  'use strict';
  const LIBRARY_URL = '/static/vendor/zxing-library-0.21.3.min.js';
  let libraryPromise;
  let session = 0;
  let stream = null;
  let reader = null;
  let videoElement = null;
  let timer = null;

  function stop() {
    session += 1;
    clearTimeout(timer);
    timer = null;
    if (reader) {
      try { reader.reset(); } catch (_) { /* Track cleanup still runs. */ }
      reader = null;
    }
    if (stream) {
      stream.getTracks().forEach(track => track.stop());
      stream = null;
    }
    if (videoElement) {
      videoElement.pause();
      videoElement.srcObject = null;
      videoElement = null;
    }
  }

  function loadLibrary() {
    if (window.ZXing && window.ZXing.BrowserMultiFormatReader) return Promise.resolve(window.ZXing);
    if (!libraryPromise) {
      libraryPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = LIBRARY_URL;
        script.async = true;
        script.onload = () => {
          if (window.ZXing && window.ZXing.BrowserMultiFormatReader) resolve(window.ZXing);
          else reject(new Error('离线扫码库加载失败，请刷新页面后重试。'));
        };
        script.onerror = () => reject(new Error('离线扫码库加载失败，请刷新页面后重试。'));
        document.head.appendChild(script);
      }).catch(error => { libraryPromise = null; throw error; });
    }
    return libraryPromise;
  }

  function friendlyError(error) {
    if (error && ['NotAllowedError', 'PermissionDeniedError', 'SecurityError'].includes(error.name)) {
      return new Error('相机权限未允许。请在浏览器设置中允许相机，或使用扫码枪和手动输入。');
    }
    if (error && ['NotFoundError', 'DevicesNotFoundError'].includes(error.name)) {
      return new Error('未找到可用相机，可使用扫码枪或手动输入条码。');
    }
    if (error && ['NotReadableError', 'TrackStartError'].includes(error.name)) {
      return new Error('相机正被其他应用占用，请关闭其他相机页面后重试。');
    }
    return error instanceof Error ? error : new Error('无法启动扫码，请使用扫码枪或手动输入。');
  }

  async function start(video, onResult, onError = () => {}) {
    stop();
    const ownSession = session;
    const active = () => ownSession === session;
    try {
      if (!video || video.tagName !== 'VIDEO' || typeof onResult !== 'function') {
        throw new Error('扫码界面尚未准备好，请重新打开扫码。');
      }
      if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('相机扫码需要 HTTPS（或本机 localhost）。当前可使用扫码枪或手动输入。');
      }
      const acquired = await navigator.mediaDevices.getUserMedia({
        audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      if (!active()) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired;
      videoElement = video;
      video.playsInline = true;
      video.muted = true;
      video.srcObject = acquired;
      await video.play();
      if (!active()) return;
      const found = value => {
        if (!active() || !value) return;
        const code = String(value).trim();
        if (!code) return;
        stop(); // One scan, then the UI asks the user to choose and confirm.
        onResult(code);
      };
      let detector = null;
      if (typeof window.BarcodeDetector === 'function') {
        try {
          const supported = await window.BarcodeDetector.getSupportedFormats();
          const wanted = ['qr_code', 'ean_13', 'ean_8', 'code_128', 'code_39', 'upc_a', 'upc_e', 'itf', 'codabar', 'data_matrix'];
          const formats = wanted.filter(format => supported.includes(format));
          // Use ZXing when the native implementation cannot handle both kinds.
          if (formats.includes('qr_code') && formats.some(format => format !== 'qr_code')) {
            detector = new window.BarcodeDetector({ formats });
          }
        } catch (_) { detector = null; }
      }
      const useZXing = async () => {
        const ZXing = await loadLibrary();
        if (!active()) return;
        const ownReader = new ZXing.BrowserMultiFormatReader(undefined, 200);
        reader = ownReader;
        // ZXing waits for a new `playing` event. Pause our already playing
        // stream first so its internal play() can emit that event again.
        video.pause();
        await ownReader.decodeFromVideoElementContinuously(video, result => {
          if (result) found(result.getText());
        });
        if (!active()) ownReader.reset();
      };
      if (!active()) return;
      if (detector) {
        const nextFrame = async () => {
          if (!active()) return;
          try {
            if (video.readyState >= 2) {
              const results = await detector.detect(video);
              if (!active()) return;
              if (results.length) { found(results[0].rawValue); return; }
            }
          } catch (_) {
            try { await useZXing(); } catch (error) {
              if (active()) { stop(); onError(friendlyError(error)); }
            }
            return;
          }
          if (active()) timer = setTimeout(nextFrame, 180);
        };
        nextFrame();
      } else {
        await useZXing();
      }
    } catch (error) {
      if (active()) { stop(); onError(friendlyError(error)); }
    }
  }

  window.addEventListener('pagehide', stop);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  window.HomeStorageScanner = Object.freeze({ start, stop });
})();
