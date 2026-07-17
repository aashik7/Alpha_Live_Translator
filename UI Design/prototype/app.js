/**
 * Alpha Live Translator — UI Showcase Prototype
 * Mock UI only. No backend, no API calls, no real exports.
 */

(function () {
  'use strict';

  // ===== Screen Navigation =====
  const navItems = document.querySelectorAll('.nav-item');
  const screens = document.querySelectorAll('.screen');

  navItems.forEach(function (item) {
    item.addEventListener('click', function () {
      const target = item.getAttribute('data-screen');

      navItems.forEach(function (nav) {
        nav.classList.remove('active');
      });
      item.classList.add('active');

      screens.forEach(function (screen) {
        screen.classList.remove('active');
      });
      const el = document.getElementById('screen-' + target);
      if (el) el.classList.add('active');
    });
  });

  // ===== Meeting Controls (Visual Only) =====
  const btnStart = document.getElementById('btn-start');
  const btnPause = document.getElementById('btn-pause');
  const btnStop = document.getElementById('btn-stop');
  const statusDot = document.getElementById('status-dot');
  const statusLabel = document.getElementById('status-label');
  const timerEl = document.getElementById('timer');

  let state = 'idle'; // idle | recording | paused
  let seconds = 0;
  let timerInterval = null;

  function formatTime(sec) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return [h, m, s].map(function (n) {
      return String(n).padStart(2, '0');
    }).join(':');
  }

  function updateTimer() {
    seconds++;
    timerEl.textContent = formatTime(seconds);
  }

  function setState(newState) {
    state = newState;

    statusDot.className = 'status-dot';
    if (state === 'recording') {
      statusDot.classList.add('recording');
      statusLabel.textContent = 'Recording';
      btnStart.disabled = true;
      btnPause.disabled = false;
      btnStop.disabled = false;
      btnPause.textContent = '⏸ Pause';
    } else if (state === 'paused') {
      statusDot.classList.add('paused');
      statusLabel.textContent = 'Paused';
      btnStart.disabled = true;
      btnPause.disabled = false;
      btnStop.disabled = false;
      btnPause.textContent = '▶ Resume';
    } else {
      statusLabel.textContent = 'Ready';
      btnStart.disabled = false;
      btnPause.disabled = true;
      btnStop.disabled = true;
      btnPause.textContent = '⏸ Pause';
      timerEl.textContent = '00:00:00';
      seconds = 0;
    }
  }

  btnStart.addEventListener('click', function () {
    if (state === 'idle') {
      setState('recording');
      timerInterval = setInterval(updateTimer, 1000);
      simulateLiveTranscript();
    }
  });

  btnPause.addEventListener('click', function () {
    if (state === 'recording') {
      setState('paused');
      clearInterval(timerInterval);
      clearInterval(liveSimInterval);
    } else if (state === 'paused') {
      setState('recording');
      timerInterval = setInterval(updateTimer, 1000);
      simulateLiveTranscript();
    }
  });

  btnStop.addEventListener('click', function () {
    clearInterval(timerInterval);
    clearInterval(liveSimInterval);
    setState('idle');
    resetLiveTranscript();
  });

  // ===== Mock Live Transcript Simulation =====
  const mockPhrases = [
    'そもそも話すことがあるかどうかという点が重要です。',
    '次のステップについてご意見をお聞かせください。',
    '資料は後ほど共有いたします。'
  ];

  const liveTranscript = document.getElementById('live-transcript');
  const stableTranscript = document.getElementById('stable-transcript');
  let phraseIndex = 0;
  let liveSimInterval = null;

  function resetLiveTranscript() {
    liveTranscript.innerHTML =
      '<p class="transcript-line stable-line">お世話になっております。</p>' +
      '<p class="transcript-line stable-line">本日は会議にご参加いただきありがとうございます。</p>' +
      '<p class="transcript-line interim-line">こちらの内容について確認させてください。<span class="cursor-blink">|</span></p>';
    stableTranscript.innerHTML =
      '<p class="transcript-line">お世話になっております。</p>' +
      '<p class="transcript-line">本日は会議にご参加いただきありがとうございます。</p>';
    phraseIndex = 0;
  }

  function simulateLiveTranscript() {
    liveSimInterval = setInterval(function () {
      if (phraseIndex >= mockPhrases.length) {
        clearInterval(liveSimInterval);
        return;
      }

      // Stabilize previous interim line
      const interim = liveTranscript.querySelector('.interim-line');
      if (interim) {
        interim.classList.remove('interim-line');
        interim.classList.add('stable-line');
        const cursor = interim.querySelector('.cursor-blink');
        if (cursor) cursor.remove();

        const stableLine = document.createElement('p');
        stableLine.className = 'transcript-line';
        stableLine.textContent = interim.textContent;
        stableTranscript.appendChild(stableLine);
      }

      // Add new interim line
      const newLine = document.createElement('p');
      newLine.className = 'transcript-line interim-line';
      newLine.innerHTML = mockPhrases[phraseIndex] + '<span class="cursor-blink">|</span>';
      liveTranscript.appendChild(newLine);
      liveTranscript.scrollTop = liveTranscript.scrollHeight;

      phraseIndex++;
    }, 4000);
  }

  // ===== Export Buttons (Visual Toast Only) =====
  const exportBtns = document.querySelectorAll('.export-btn');
  const toast = document.getElementById('export-toast');
  let toastTimeout = null;

  exportBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      const format = btn.getAttribute('data-format');
      const type = btn.getAttribute('data-type');
      toast.textContent = format + ' export (' + type + ') — preview only, no file generated';
      toast.classList.remove('hidden');
      toast.classList.add('visible');

      clearTimeout(toastTimeout);
      toastTimeout = setTimeout(function () {
        toast.classList.remove('visible');
        toast.classList.add('hidden');
      }, 3000);
    });
  });

})();
