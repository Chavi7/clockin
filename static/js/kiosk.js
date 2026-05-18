/* ============================================================
   KIOSK · scanner + camera + manual input controller
   ============================================================ */
(function () {
  'use strict';

  // ---------- DOM ----------
  const tabs        = document.querySelectorAll('.tab');
  const paneScanner = document.getElementById('paneScanner');
  const paneCamera  = document.getElementById('paneCamera');
  const paneManual  = document.getElementById('paneManual');
  const scannerInput = document.getElementById('scannerInput');
  const manualForm  = document.getElementById('manualForm');
  const manualInput = document.getElementById('manualInput');
  const cameraVideo = document.getElementById('cameraVideo');
  const cameraCanvas = document.getElementById('cameraCanvas');
  const cameraHint  = document.getElementById('cameraHint');
  const idleStage   = document.getElementById('idleStage');
  const resultStage = document.getElementById('resultStage');
  const resultCard  = document.getElementById('resultCard');
  const resultIcon  = document.getElementById('resultIcon');
  const resultAction = document.getElementById('resultAction');
  const resultName  = document.getElementById('resultName');
  const resultMeta  = document.getElementById('resultMeta');
  const resultTime  = document.getElementById('resultTime');
  const resetCountdown = document.getElementById('resetCountdown');
  const resetNow    = document.getElementById('resetNow');
  const liveClock   = document.getElementById('liveClock');
  const liveDate    = document.getElementById('liveDate');

  let currentMode = 'scanner';
  let cameraStream = null;
  let cameraLoopId = null;
  let busy = false;
  let countdownTimer = null;

  // ---------- LIVE CLOCK ----------
  function updateClock() {
    const now = new Date();
    let h = now.getHours();
    const m = now.getMinutes().toString().padStart(2, '0');
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    liveClock.textContent = h + ':' + m + ' ' + ampm;
    liveDate.textContent = now.toLocaleDateString('en-US', {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
    }).toUpperCase();
  }
  updateClock();
  setInterval(updateClock, 1000 * 30);

  // ---------- MODE SWITCHING ----------
  function setMode(mode) {
    currentMode = mode;
    tabs.forEach(t => t.classList.toggle('active', t.dataset.mode === mode));
    paneScanner.classList.toggle('active', mode === 'scanner');
    paneCamera.classList.toggle('active', mode === 'camera');
    paneManual.classList.toggle('active', mode === 'manual');

    stopCamera();

    if (mode === 'scanner') {
      setTimeout(() => scannerInput.focus(), 50);
    } else if (mode === 'camera') {
      startCamera();
    } else if (mode === 'manual') {
      setTimeout(() => manualInput.focus(), 50);
    }
  }

  tabs.forEach(t => t.addEventListener('click', () => setMode(t.dataset.mode)));

  // ---------- USB SCANNER ----------
  // USB barcode/QR scanners act as keyboards: they "type" the content then send Enter.
  // We capture into a hidden input and submit on Enter.
  scannerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const raw = scannerInput.value.trim();
      scannerInput.value = '';
      if (raw) submitScan(raw);
    }
  });

  // Keep focus on the scanner input when in scanner mode so any keypress is captured.
  document.addEventListener('click', () => {
    if (currentMode === 'scanner' && !busy) scannerInput.focus();
  });
  document.addEventListener('keydown', (e) => {
    if (currentMode === 'scanner' && document.activeElement !== scannerInput && !busy) {
      scannerInput.focus();
    }
  });

  // ---------- MANUAL ----------
  manualForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const raw = manualInput.value.trim().toUpperCase();
    manualInput.value = '';
    if (raw) submitScan(raw);
  });

  // ---------- CAMERA ----------
  async function startCamera() {
    if (cameraStream) return;
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' }
      });
      cameraVideo.srcObject = cameraStream;
      cameraVideo.setAttribute('playsinline', true);
      await cameraVideo.play();
      cameraHint.textContent = 'Position the QR code inside the frame.';
      tickCamera();
    } catch (err) {
      cameraHint.textContent = 'Could not access the camera. Use the USB SCANNER or TYPE ID tabs instead.';
      console.error('Camera error:', err);
    }
  }

  function stopCamera() {
    if (cameraLoopId) {
      cancelAnimationFrame(cameraLoopId);
      cameraLoopId = null;
    }
    if (cameraStream) {
      cameraStream.getTracks().forEach(t => t.stop());
      cameraStream = null;
      cameraVideo.srcObject = null;
    }
  }

  function tickCamera() {
    if (!cameraStream || busy) {
      cameraLoopId = requestAnimationFrame(tickCamera);
      return;
    }
    if (cameraVideo.readyState === cameraVideo.HAVE_ENOUGH_DATA) {
      cameraCanvas.width  = cameraVideo.videoWidth;
      cameraCanvas.height = cameraVideo.videoHeight;
      const ctx = cameraCanvas.getContext('2d');
      ctx.drawImage(cameraVideo, 0, 0, cameraCanvas.width, cameraCanvas.height);
      const imageData = ctx.getImageData(0, 0, cameraCanvas.width, cameraCanvas.height);

      if (window.jsQR) {
        const code = window.jsQR(imageData.data, imageData.width, imageData.height, {
          inversionAttempts: 'dontInvert'
        });
        if (code && code.data) {
          submitScan(code.data);
          return; // pause loop while we wait for the server
        }
      }
    }
    cameraLoopId = requestAnimationFrame(tickCamera);
  }

  // ---------- SUBMIT ----------
  async function submitScan(raw) {
    if (busy) return;
    busy = true;

    try {
      const res = await fetch('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw })
      });
      const data = await res.json();
      showResult(res.ok, data);
    } catch (err) {
      showResult(false, { error: 'Network error. Try again.' });
    }
  }

  // ---------- RESULT DISPLAY ----------
  function showResult(ok, data) {
    idleStage.hidden = true;
    resultStage.hidden = false;

    resultCard.classList.remove('is-clock-in', 'is-clock-out', 'is-error');

    if (!ok || !data.ok) {
      resultCard.classList.add('is-error');
      resultIcon.textContent = '⚠';
      resultAction.textContent = 'NOT RECOGNIZED';
      resultName.textContent = data.name || 'See a Manager';
      resultMeta.textContent = '';
      resultTime.textContent = data.error || 'Try again.';
    } else if (data.action === 'clock_in') {
      resultCard.classList.add('is-clock-in');
      resultIcon.textContent = '✓';
      resultAction.textContent = 'CLOCKED IN';
      resultName.textContent = data.name;
      resultMeta.textContent = (data.role || '') +
        (data.period ? ' · Period ' + data.period : '');
      resultTime.textContent = data.time;
    } else if (data.action === 'clock_out') {
      resultCard.classList.add('is-clock-out');
      resultIcon.textContent = '⤶';
      resultAction.textContent = 'CLOCKED OUT';
      resultName.textContent = data.name;
      resultMeta.textContent = (data.duration ? 'Shift: ' + data.duration : '') +
        (data.period ? ' · Period ' + data.period : '');
      resultTime.textContent = data.time;
    }

    // Auto-return after 5 seconds
    let secs = 5;
    resetCountdown.textContent = secs;
    clearInterval(countdownTimer);
    countdownTimer = setInterval(() => {
      secs--;
      resetCountdown.textContent = secs;
      if (secs <= 0) {
        clearInterval(countdownTimer);
        returnToIdle();
      }
    }, 1000);
  }

  resetNow.addEventListener('click', () => {
    clearInterval(countdownTimer);
    returnToIdle();
  });

  function returnToIdle() {
    resultStage.hidden = true;
    idleStage.hidden = false;
    busy = false;
    if (currentMode === 'scanner') scannerInput.focus();
    if (currentMode === 'manual') manualInput.focus();
  }

  // ---------- BOOT ----------
  setMode('scanner');
})();
