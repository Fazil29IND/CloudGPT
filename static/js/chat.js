/* Chat UI deliberately builds user/model content with DOM APIs, never innerHTML. */
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('chat-input');
  const send = document.getElementById('send-btn');
  const container = document.getElementById('chat-container');
  const historyList = document.getElementById('history-list');
  const historySearchInput = document.getElementById('history-search-input');
  const historySearchClear = document.getElementById('history-search-clear');
  const title = document.getElementById('current-chat-title');
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const newChat = document.getElementById('new-chat-btn');
  
  // Model selector elements (scoped to the model popover only — the thinking
  // popover reuses .model-option styling but must not share selection state)
  const modelSelectorBtn = document.getElementById('model-selector-btn');
  const modelPopover = document.getElementById('model-popover');
  const selectedModelName = document.getElementById('selected-model-name');
  const modelOptions = document.querySelectorAll('#model-popover .model-option');

  // Thinking mode selector elements
  const thinkingSelectorBtn = document.getElementById('thinking-selector-btn');
  const thinkingPopover = document.getElementById('thinking-popover');
  const selectedThinkingLevel = document.getElementById('selected-thinking-level');
  const thinkingOptions = document.querySelectorAll('#thinking-popover .model-option');
  
  // File upload elements
  const attachBtn = document.getElementById('attach-btn');
  const localFilePicker = document.getElementById('local-file-picker');
  const fileChipContainer = document.getElementById('file-chip-container');
  const micBtn = document.getElementById('mic-btn');

  // Settings modal elements
  const settingsBtn = document.getElementById('settings-btn');
  const settingsModal = document.getElementById('settings-modal');
  const settingsModalClose = document.getElementById('settings-modal-close');
  const settingsTabs = document.querySelectorAll('.settings-tab');
  const settingsPanels = document.querySelectorAll('.settings-panel');

  // Settings — Account tab
  const settingsNameInput = document.getElementById('settings-name-input');
  const settingsSaveNameBtn = document.getElementById('settings-save-name-btn');
  const settingsNameFeedback = document.getElementById('settings-name-feedback');
  const settingsChangePasswordToggle = document.getElementById('settings-change-password-toggle');
  const settingsPasswordFields = document.getElementById('settings-password-fields');
  const settingsSavePasswordBtn = document.getElementById('settings-save-password-btn');
  const settingsCancelPasswordBtn = document.getElementById('settings-cancel-password-btn');
  const settingsPasswordFeedback = document.getElementById('settings-password-feedback');

  // Settings — Memory tab
  const settingsMemoryLoading = document.getElementById('settings-memory-loading');
  const settingsMemoryEmpty = document.getElementById('settings-memory-empty');
  const settingsMemoryItems = document.getElementById('settings-memory-items');
  const settingsClearAllMemoryBtn = document.getElementById('settings-clear-all-memory-btn');
  const settingsClearMemoryConfirm = document.getElementById('settings-clear-memory-confirm');
  const settingsClearMemoryYes = document.getElementById('settings-clear-memory-yes');
  const settingsClearMemoryNo = document.getElementById('settings-clear-memory-no');

  // Settings — Preferences tab
  const prefToggles = document.querySelectorAll('.settings-toggle-row[data-pref-key] .settings-toggle, .settings-toggle[data-pref-key]');
  const prefSelects = document.querySelectorAll('.settings-select[data-pref-key]');

  // Settings — Subscription tab
  const settingsBillingPortalBtn = document.getElementById('settings-billing-portal-btn');

  // Memory toast (kept — driven by SSE, unrelated to modal)
  const memoryToast = document.getElementById('memory-toast');
  const memoryToastText = document.getElementById('memory-toast-text');
  let memoryToastTimeout = null;

  // Mobile sidebar drawer elements
  const mobileSidebarToggle = document.getElementById('mobile-sidebar-toggle');
  const sidebarOverlay = document.getElementById('sidebar-overlay');
  const sidebar = document.querySelector('.sidebar');

  let sessionId = generateUUID();
  let sessions = [];
  let currentSearchQuery = '';
  let isNew = true;
  let activeController = null;
  let selectedModel = (selectedModelName && selectedModelName.textContent.trim()) || 'Apex';
  let selectedThinking = (selectedThinkingLevel && selectedThinkingLevel.textContent.trim()) || 'Medium';
  let stagedFiles = [];

  // UUID generation with fallback for browsers without crypto.randomUUID
  function generateUUID() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return Date.now().toString(36) + Math.random().toString(36).substring(2);
  }

  function scrollToBottom() { container.scrollTop = container.scrollHeight; }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* ── Mobile Sidebar Drawer Controls ── */
  function openMobileSidebar() {
    sidebar?.classList.add('open');
    sidebarOverlay?.classList.add('active');
  }

  function closeMobileSidebar() {
    sidebar?.classList.remove('open');
    sidebarOverlay?.classList.remove('active');
  }

  /* ── ChatGPT-Style Greeting ── */
  function getGreetingDetails() {
    const hour = new Date().getHours();
    let timePeriod = 'morning';
    if (hour >= 12 && hour < 17) {
      timePeriod = 'afternoon';
    } else if (hour >= 17 || hour < 5) {
      timePeriod = 'evening';
    }

    const appEl = document.querySelector('.app-container');
    const rawName = (appEl?.dataset?.userName || document.querySelector('.user-profile .name')?.textContent || '').trim();
    const username = rawName ? rawName.split('@')[0].trim() : '';

    if (username) {
      return {
        title: `Good ${timePeriod}, ${username}`,
        subtitle: 'What can I help with today?'
      };
    }
    return {
      title: 'What can I help with today?',
      subtitle: 'Ask anything about cloud architecture, pricing, troubleshooting, and security.'
    };
  }

  function updateGreetingText() {
    const titleEl = document.getElementById('chatgpt-greeting-title');
    const subEl = document.getElementById('chatgpt-greeting-subtitle');
    if (titleEl || subEl) {
      const details = getGreetingDetails();
      if (titleEl) titleEl.textContent = details.title;
      if (subEl) subEl.textContent = details.subtitle;
    }
  }

  function initStarterCards() {
    updateGreetingText();
  }

  function renderStarterCards() {
    if (!container) return;
    if (document.getElementById('chat-empty-state')) return;

    const emptyDiv = element('div', 'chat-empty-state chatgpt-empty-state');
    emptyDiv.id = 'chat-empty-state';

    const greetingDetails = getGreetingDetails();

    const wrapper = element('div', 'chatgpt-greeting-wrapper');
    const titleH1 = element('h1', 'chatgpt-greeting-title', greetingDetails.title);
    titleH1.id = 'chatgpt-greeting-title';
    const subP = element('p', 'chatgpt-greeting-subtitle', greetingDetails.subtitle);
    subP.id = 'chatgpt-greeting-subtitle';
    wrapper.append(titleH1, subP);

    emptyDiv.append(wrapper);
    container.append(emptyDiv);
  }

  function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function updateSendState() {
    const hasText = Boolean(input.value.trim());
    const hasFiles = stagedFiles.length > 0;
    send.disabled = !(hasText || hasFiles);
  }

  function renderFileChips() {
    if (!fileChipContainer) return;
    fileChipContainer.replaceChildren();
    
    if (stagedFiles.length === 0) {
      fileChipContainer.classList.add('hidden');
      updateSendState();
      return;
    }

    fileChipContainer.classList.remove('hidden');
    stagedFiles.forEach((file, index) => {
      const chip = element('div', 'file-chip');
      
      const icon = element('span', 'file-chip-icon');
      const iconSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      iconSvg.setAttribute('viewBox', '0 0 24 24');
      iconSvg.setAttribute('width', '13');
      iconSvg.setAttribute('height', '13');
      iconSvg.setAttribute('fill', 'none');
      iconSvg.setAttribute('stroke', 'currentColor');
      iconSvg.setAttribute('stroke-width', '2');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', 'M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z');
      const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
      polyline.setAttribute('points', '13 2 13 9 20 9');
      iconSvg.append(path, polyline);
      icon.append(iconSvg);

      const nameSpan = element('span', 'file-chip-name', file.name);
      nameSpan.title = file.name;

      const sizeSpan = element('span', 'file-chip-size', formatFileSize(file.size));

      const removeBtn = element('button', 'file-chip-remove');
      removeBtn.type = 'button';
      removeBtn.title = 'Remove file';
      const closeSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      closeSvg.setAttribute('viewBox', '0 0 24 24');
      closeSvg.setAttribute('width', '12');
      closeSvg.setAttribute('height', '12');
      closeSvg.setAttribute('fill', 'none');
      closeSvg.setAttribute('stroke', 'currentColor');
      closeSvg.setAttribute('stroke-width', '2');
      const l1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      l1.setAttribute('x1', '18'); l1.setAttribute('y1', '6'); l1.setAttribute('x2', '6'); l1.setAttribute('y2', '18');
      const l2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      l2.setAttribute('x1', '6'); l2.setAttribute('y1', '6'); l2.setAttribute('x2', '18'); l2.setAttribute('y2', '18');
      closeSvg.append(l1, l2);
      removeBtn.append(closeSvg);

      removeBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        stagedFiles.splice(index, 1);
        renderFileChips();
      });

      chip.append(icon, nameSpan, sizeSpan, removeBtn);
      fileChipContainer.append(chip);
    });

    updateSendState();
  }

  // Model Selection Dropdown Toggle
  if (modelSelectorBtn && modelPopover) {
    modelSelectorBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isHidden = modelPopover.classList.contains('hidden');
      if (isHidden) {
        modelPopover.classList.remove('hidden');
        modelSelectorBtn.setAttribute('aria-expanded', 'true');
        if (typeof thinkingPopover !== 'undefined' && thinkingPopover && !thinkingPopover.classList.contains('hidden')) {
          thinkingPopover.classList.add('hidden');
          if (typeof thinkingSelectorBtn !== 'undefined' && thinkingSelectorBtn) {
            thinkingSelectorBtn.setAttribute('aria-expanded', 'false');
          }
        }
      } else {
        modelPopover.classList.add('hidden');
        modelSelectorBtn.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('click', (event) => {
      if (!modelSelectorBtn.contains(event.target) && !modelPopover.contains(event.target)) {
        modelPopover.classList.add('hidden');
        modelSelectorBtn.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !modelPopover.classList.contains('hidden')) {
        modelPopover.classList.add('hidden');
        modelSelectorBtn.setAttribute('aria-expanded', 'false');
        modelSelectorBtn.focus();
      }
    });

    const appContainer = document.querySelector('.app-container');
    const userTier = (appContainer?.dataset?.userTier || 'Free').trim();

    const TIER_MODEL_THINKING = {
      'Free': {
        'Lite': ['Low', 'Medium', 'High'],
        'Core': ['Low', 'Medium'],
        'Apex': ['Low']
      },
      'Lite': {
        'Lite': ['Low', 'Medium', 'High'],
        'Core': ['Low', 'Medium'],
        'Apex': ['Low']
      },
      'Pro': {
        'Lite': ['Low', 'Medium', 'High', 'Max'],
        'Core': ['Low', 'Medium', 'High'],
        'Apex': ['Low', 'Medium']
      },
      'Core': {
        'Lite': ['Low', 'Medium', 'High', 'Max'],
        'Core': ['Low', 'Medium', 'High'],
        'Apex': ['Low', 'Medium']
      },
      'Max': {
        'Lite': ['Low', 'Medium', 'High', 'Max'],
        'Core': ['Low', 'Medium', 'High', 'Max'],
        'Apex': ['Low', 'Medium', 'High', 'Max']
      },
      'Apex': {
        'Lite': ['Low', 'Medium', 'High', 'Max'],
        'Core': ['Low', 'Medium', 'High', 'Max'],
        'Apex': ['Low', 'Medium', 'High', 'Max']
      },
      'Developer': {
        'Lite': ['Low', 'Medium', 'High', 'Max'],
        'Core': ['Low', 'Medium', 'High', 'Max'],
        'Apex': ['Low', 'Medium', 'High', 'Max']
      }
    };

    function updateThinkingOptionsForModel(modelName) {
      const tierMap = TIER_MODEL_THINKING[userTier] || TIER_MODEL_THINKING['Free'];
      const allowedForModel = tierMap[modelName] || ['Low', 'Medium', 'High'];
      
      thinkingOptions.forEach((option) => {
        const level = option.dataset.thinking;
        if (allowedForModel.includes(level)) {
          option.classList.remove('disabled');
          option.dataset.tierAllowed = 'true';
        } else {
          option.classList.add('disabled');
          option.dataset.tierAllowed = 'false';
          option.classList.remove('active');
        }
      });

      const currentActiveOption = document.querySelector(`#thinking-popover .model-option[data-thinking="${selectedThinking}"]`);
      if (!currentActiveOption || currentActiveOption.classList.contains('disabled')) {
        const enabledOptions = Array.from(thinkingOptions).filter(opt => !opt.classList.contains('disabled'));
        if (enabledOptions.length > 0) {
          const fallbackOption = enabledOptions[enabledOptions.length - 1];
          selectedThinking = fallbackOption.dataset.thinking;
        } else {
          selectedThinking = 'Low';
        }
        if (selectedThinkingLevel) selectedThinkingLevel.textContent = selectedThinking;
        
        thinkingOptions.forEach(opt => opt.classList.remove('active'));
        const newActive = document.querySelector(`#thinking-popover .model-option[data-thinking="${selectedThinking}"]`);
        if (newActive) newActive.classList.add('active');
      }
    }

    modelOptions.forEach((option) => {
      option.addEventListener('click', () => {
        if (option.classList.contains('disabled') || option.dataset.tierAllowed === 'false') return;
        const model = option.dataset.model;
        if (model) {
          selectedModel = model;
          if (selectedModelName) selectedModelName.textContent = model;
          modelOptions.forEach((opt) => opt.classList.remove('active'));
          option.classList.add('active');
          modelPopover.classList.add('hidden');
          modelSelectorBtn.setAttribute('aria-expanded', 'false');
          updateThinkingOptionsForModel(model);
        }
      });
    });

    // Synchronize initial model state on load
    updateThinkingOptionsForModel(selectedModel);
  }

  // Thinking Depth Selection Dropdown
  if (thinkingSelectorBtn && thinkingPopover) {
    thinkingSelectorBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isHidden = thinkingPopover.classList.contains('hidden');
      if (isHidden) {
        thinkingPopover.classList.remove('hidden');
        thinkingSelectorBtn.setAttribute('aria-expanded', 'true');
        if (typeof modelPopover !== 'undefined' && modelPopover && !modelPopover.classList.contains('hidden')) {
          modelPopover.classList.add('hidden');
          if (typeof modelSelectorBtn !== 'undefined' && modelSelectorBtn) {
            modelSelectorBtn.setAttribute('aria-expanded', 'false');
          }
        }
      } else {
        thinkingPopover.classList.add('hidden');
        thinkingSelectorBtn.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('click', (event) => {
      if (!thinkingSelectorBtn.contains(event.target) && !thinkingPopover.contains(event.target)) {
        thinkingPopover.classList.add('hidden');
        thinkingSelectorBtn.setAttribute('aria-expanded', 'false');
      }
    });

    thinkingOptions.forEach((option) => {
      option.addEventListener('click', () => {
        if (option.classList.contains('disabled')) return;
        const level = option.dataset.thinking;
        if (level) {
          selectedThinking = level;
          if (selectedThinkingLevel) selectedThinkingLevel.textContent = level;
          thinkingOptions.forEach((opt) => opt.classList.remove('active'));
          option.classList.add('active');
          thinkingPopover.classList.add('hidden');
          thinkingSelectorBtn.setAttribute('aria-expanded', 'false');
        }
      });
    });
  }

  // Local file picker trigger on '+' click
  if (attachBtn && localFilePicker) {
    attachBtn.addEventListener('click', () => {
      localFilePicker.click();
    });

    localFilePicker.addEventListener('change', (event) => {
      const files = Array.from(event.target.files || []);
      if (files.length > 0) {
        files.forEach((f) => stagedFiles.push(f));
        renderFileChips();
      }
      localFilePicker.value = '';
    });
  }

  // Voice Recognition & Live Audio Visualizer Controller
  const audioVisualizer = document.getElementById('audio-visualizer');
  const waveBars = audioVisualizer ? audioVisualizer.querySelectorAll('.wave-bar') : [];
  
  let isListening = false;
  let recognition = null;
  let audioStream = null;
  let audioContext = null;
  let analyserNode = null;
  let animFrameId = null;
  let silenceTimer = null;
  let baseTextBeforeSpeech = '';

  function stopAudioVisualizer() {
    if (animFrameId) {
      cancelAnimationFrame(animFrameId);
      animFrameId = null;
    }
    if (audioStream) {
      audioStream.getTracks().forEach((track) => track.stop());
      audioStream = null;
    }
    if (audioContext && audioContext.state !== 'closed') {
      audioContext.close().catch(() => {});
      audioContext = null;
    }
    waveBars.forEach((bar) => {
      bar.style.height = '';
      bar.style.transform = '';
    });
    if (audioVisualizer) audioVisualizer.classList.add('hidden');
  }

  async function startAudioVisualizer() {
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
      audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      audioContext = new AudioCtx();
      analyserNode = audioContext.createAnalyser();
      analyserNode.fftSize = 64;
      analyserNode.smoothingTimeConstant = 0.8;
      
      const source = audioContext.createMediaStreamSource(audioStream);
      source.connect(analyserNode);

      const bufferLength = analyserNode.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      if (audioVisualizer) audioVisualizer.classList.remove('hidden');

      function updateVisualizer() {
        if (!isListening) return;
        analyserNode.getByteFrequencyData(dataArray);
        
        let sum = 0;
        for (let i = 0; i < bufferLength; i += 1) {
          sum += dataArray[i];
        }
        const average = sum / bufferLength;
        const normalized = Math.min(Math.max(average / 128, 0.2), 1.8);

        waveBars.forEach((bar, index) => {
          const freqValue = dataArray[index * 2] || average;
          const height = Math.min(Math.max((freqValue / 255) * 20, 4), 22);
          bar.style.height = `${height}px`;
          bar.style.transform = `scaleY(${normalized})`;
        });

        animFrameId = requestAnimationFrame(updateVisualizer);
      }

      updateVisualizer();
    } catch (err) {
      console.warn('Audio waveform visualizer unavailable:', err);
    }
  }

  function resetSilenceTimer() {
    if (silenceTimer) clearTimeout(silenceTimer);
    silenceTimer = setTimeout(() => {
      if (isListening) {
        stopVoiceDictation();
      }
    }, 4500); // 4.5s silence auto-stop
  }

  function stopVoiceDictation() {
    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
    isListening = false;
    if (recognition) {
      try { recognition.stop(); } catch (_) {}
    }
    if (micBtn) {
      micBtn.classList.remove('listening');
      micBtn.setAttribute('title', 'Voice Input (Ctrl+M)');
    }
    stopAudioVisualizer();
    input.focus();
  }

  function startVoiceDictation() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      alert('Voice dictation is not supported by your current browser. Please use Chrome, Edge, or Safari.');
      return;
    }

    try {
      recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = 'en-US';

      baseTextBeforeSpeech = input.value.trim();

      recognition.onstart = () => {
        isListening = true;
        if (micBtn) {
          micBtn.classList.add('listening');
          micBtn.setAttribute('title', 'Listening... click to stop (Ctrl+M)');
        }
        startAudioVisualizer();
        resetSilenceTimer();
      };

      recognition.onresult = (event) => {
        resetSilenceTimer();
        let interimTranscript = '';
        let finalTranscript = '';

        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const text = event.results[i][0].transcript;
          if (event.results[i].isFinal) {
            finalTranscript += text;
          } else {
            interimTranscript += text;
          }
        }

        const combinedSpeech = (finalTranscript + ' ' + interimTranscript).trim();
        if (combinedSpeech) {
          if (baseTextBeforeSpeech) {
            input.value = baseTextBeforeSpeech + ' ' + combinedSpeech;
          } else {
            input.value = combinedSpeech;
          }
          input.style.height = 'auto';
          input.style.height = `${input.scrollHeight}px`;
          updateSendState();
          scrollToBottom();
        }
      };

      recognition.onerror = (event) => {
        console.warn('Speech recognition notification:', event.error);
        if (event.error === 'not-allowed') {
          alert('Microphone permission was denied. Please allow microphone access in your browser settings.');
        }
        stopVoiceDictation();
      };

      recognition.onend = () => {
        if (isListening) {
          stopVoiceDictation();
        }
      };

      recognition.start();
    } catch (err) {
      console.error('Failed to initialize speech recognition:', err);
      stopVoiceDictation();
    }
  }

  function toggleVoiceDictation() {
    if (isListening) {
      stopVoiceDictation();
    } else {
      startVoiceDictation();
    }
  }

  if (micBtn) {
    micBtn.addEventListener('click', (e) => {
      e.preventDefault();
      toggleVoiceDictation();
    });
  }

  // Global keyboard shortcuts
  document.addEventListener('keydown', (event) => {
    // Ctrl/Cmd + M: Toggle voice dictation
    if ((event.ctrlKey || event.metaKey) && (event.key === 'm' || event.key === 'M')) {
      event.preventDefault();
      toggleVoiceDictation();
    }
    // Ctrl/Cmd + K: Focus prompt input / search
    if ((event.ctrlKey || event.metaKey) && (event.key === 'k' || event.key === 'K')) {
      event.preventDefault();
      const searchInput = document.getElementById('session-search-input');
      if (searchInput && document.activeElement !== searchInput) {
        searchInput.focus();
      } else if (input) {
        input.focus();
      }
    }
    // Escape: Abort ongoing stream or close settings modal
    if (event.key === 'Escape') {
      if (settingsModal && !settingsModal.classList.contains('hidden')) {
        closeSettingsModal();
      } else if (activeController) {
        activeController.abort();
      }
    }
    // Ctrl+, or Cmd+, opens settings
    if ((event.ctrlKey || event.metaKey) && event.key === ',') {
      event.preventDefault();
      openSettingsModal('account');
    }
  });

  // ── User Memory Modal & Toast Functions ──────────────────────────────────
  function showMemoryToast(text) {
    if (!memoryToast || !memoryToastText) return;
    memoryToastText.textContent = text;
    memoryToast.classList.remove('hidden');
    if (memoryToastTimeout) clearTimeout(memoryToastTimeout);
    memoryToastTimeout = setTimeout(() => {
      memoryToast.classList.add('hidden');
    }, 4000);
  }

  let currentSettingsTab = 'account';

  function openSettingsModal(tab = 'account') {
    if (!settingsModal) return;
    settingsModal.classList.remove('hidden');
    switchSettingsTab(tab);
  }

  function closeSettingsModal() {
    if (settingsModal) settingsModal.classList.add('hidden');
  }

  function switchSettingsTab(tab) {
    currentSettingsTab = tab;
    settingsTabs.forEach((t) => {
      const isActive = t.dataset.tab === tab;
      t.classList.toggle('active', isActive);
      t.setAttribute('aria-selected', String(isActive));
    });
    settingsPanels.forEach((p) => {
      const isActive = p.dataset.panel === tab;
      p.classList.toggle('active', isActive);
      p.classList.toggle('hidden', !isActive);
    });
    if (tab === 'memory') loadSettingsMemory();
    if (tab === 'subscription') loadSettingsSubscription();
  }

  settingsTabs.forEach((tab) => {
    tab.addEventListener('click', () => switchSettingsTab(tab.dataset.tab));
  });
  if (settingsBtn) settingsBtn.addEventListener('click', () => openSettingsModal('account'));
  if (settingsModalClose) settingsModalClose.addEventListener('click', closeSettingsModal);
  if (settingsModal) {
    settingsModal.addEventListener('click', (e) => {
      if (e.target === settingsModal) closeSettingsModal();
    });
  }

  function showSettingsFeedback(el, type, msg) {
    if (!el) return;
    el.textContent = msg;
    el.className = `settings-feedback ${type}`;
    setTimeout(() => {
      if (el && el.textContent === msg) el.textContent = '';
    }, 4000);
  }

  // Settings: Account tab - Save name
  if (settingsSaveNameBtn) {
    settingsSaveNameBtn.addEventListener('click', async () => {
      const name = settingsNameInput?.value.trim() || '';
      if (!name) {
        showSettingsFeedback(settingsNameFeedback, 'error', 'Name cannot be empty.');
        return;
      }
      settingsSaveNameBtn.disabled = true;
      settingsSaveNameBtn.textContent = 'Saving…';
      try {
        const res = await fetch('/api/user/profile', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
          body: JSON.stringify({ name }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || 'Failed to save name');
        }
        showSettingsFeedback(settingsNameFeedback, 'success', 'Name updated.');
        const nameEl = document.querySelector('.user-profile .name');
        const avatarEl = document.querySelector('.user-profile .avatar');
        if (nameEl) nameEl.textContent = name;
        if (avatarEl) avatarEl.textContent = name[0].toUpperCase();
      } catch (err) {
        showSettingsFeedback(settingsNameFeedback, 'error', err.message);
      } finally {
        settingsSaveNameBtn.disabled = false;
        settingsSaveNameBtn.textContent = 'Save';
      }
    });
  }

  // Settings: Account tab - Password change
  if (settingsChangePasswordToggle) {
    settingsChangePasswordToggle.addEventListener('click', () => {
      settingsPasswordFields?.classList.toggle('hidden');
    });
  }

  if (settingsCancelPasswordBtn) {
    settingsCancelPasswordBtn.addEventListener('click', () => {
      settingsPasswordFields?.classList.add('hidden');
      if (settingsPasswordFeedback) settingsPasswordFeedback.textContent = '';
      const cur = document.getElementById('settings-current-password');
      const np = document.getElementById('settings-new-password');
      const cp = document.getElementById('settings-confirm-password');
      if (cur) cur.value = '';
      if (np) np.value = '';
      if (cp) cp.value = '';
    });
  }

  if (settingsSavePasswordBtn) {
    settingsSavePasswordBtn.addEventListener('click', async () => {
      const current = document.getElementById('settings-current-password')?.value || '';
      const newPwd = document.getElementById('settings-new-password')?.value || '';
      const confirm = document.getElementById('settings-confirm-password')?.value || '';
      if (!current || !newPwd || !confirm) {
        showSettingsFeedback(settingsPasswordFeedback, 'error', 'All fields are required.');
        return;
      }
      settingsSavePasswordBtn.disabled = true;
      settingsSavePasswordBtn.textContent = 'Updating…';
      try {
        const res = await fetch('/api/user/change-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
          body: JSON.stringify({ current_password: current, new_password: newPwd, confirm_password: confirm }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Password update failed');
        showSettingsFeedback(settingsPasswordFeedback, 'success', 'Password updated successfully.');
        settingsPasswordFields?.classList.add('hidden');
        const cur = document.getElementById('settings-current-password');
        const np = document.getElementById('settings-new-password');
        const cp = document.getElementById('settings-confirm-password');
        if (cur) cur.value = '';
        if (np) np.value = '';
        if (cp) cp.value = '';
      } catch (err) {
        showSettingsFeedback(settingsPasswordFeedback, 'error', err.message);
      } finally {
        settingsSavePasswordBtn.disabled = false;
        settingsSavePasswordBtn.textContent = 'Update Password';
      }
    });
  }

  // Settings: Memory tab - Load & Delete
  async function loadSettingsMemory() {
    if (!settingsMemoryItems) return;
    if (settingsMemoryLoading) settingsMemoryLoading.classList.remove('hidden');
    if (settingsMemoryEmpty) settingsMemoryEmpty.classList.add('hidden');
    settingsMemoryItems.replaceChildren();

    try {
      const resp = await fetch('/api/user/memory', {
        headers: { 'Accept': 'application/json' },
      });
      if (!resp.ok) throw new Error('Failed to load memory');
      const data = await resp.json();
      const list = data.memories || [];

      if (settingsMemoryLoading) settingsMemoryLoading.classList.add('hidden');
      if (list.length === 0) {
        if (settingsMemoryEmpty) settingsMemoryEmpty.classList.remove('hidden');
        return;
      }

      list.forEach((mem) => {
        const card = element('div', 'memory-item-card');
        const content = element('div', 'memory-item-content');
        const header = element('div', 'memory-item-header');
        const badge = element('span', 'memory-category-badge', mem.category || 'general');
        const keyName = element('span', 'memory-key-name', mem.memory_key || mem.key || 'Preference');
        header.append(badge, keyName);

        const val = element('div', 'memory-item-value', mem.memory_value || mem.value || '');
        content.append(header, val);

        const delBtn = element('button', 'memory-delete-btn', 'Delete');
        delBtn.type = 'button';
        delBtn.title = 'Forget this preference';
        delBtn.addEventListener('click', async () => {
          delBtn.disabled = true;
          delBtn.textContent = '...';
          try {
            const delResp = await fetch(`/api/user/memory/${mem.id}`, {
              method: 'DELETE',
              headers: { 'X-CSRF-Token': csrfToken },
            });
            if (delResp.ok) {
              card.remove();
              if (settingsMemoryItems && settingsMemoryItems.children.length === 0 && settingsMemoryEmpty) {
                settingsMemoryEmpty.classList.remove('hidden');
              }
            } else {
              delBtn.textContent = 'Error';
              delBtn.disabled = false;
            }
          } catch (_) {
            delBtn.textContent = 'Error';
            delBtn.disabled = false;
          }
        });

        card.append(content, delBtn);
        settingsMemoryItems.append(card);
      });
    } catch {
      if (settingsMemoryLoading) settingsMemoryLoading.textContent = 'Failed to load preferences.';
    }
  }

  // Settings: Memory tab - Clear All
  if (settingsClearAllMemoryBtn) {
    settingsClearAllMemoryBtn.addEventListener('click', () => {
      settingsClearMemoryConfirm?.classList.remove('hidden');
    });
  }

  if (settingsClearMemoryNo) {
    settingsClearMemoryNo.addEventListener('click', () => {
      settingsClearMemoryConfirm?.classList.add('hidden');
    });
  }

  if (settingsClearMemoryYes) {
    settingsClearMemoryYes.addEventListener('click', async () => {
      settingsClearMemoryYes.disabled = true;
      settingsClearMemoryYes.textContent = 'Deleting…';
      try {
        const res = await fetch('/api/user/memory', {
          method: 'DELETE',
          headers: { 'X-CSRF-Token': csrfToken },
        });
        if (!res.ok) throw new Error('Clear failed');
        settingsClearMemoryConfirm?.classList.add('hidden');
        settingsMemoryItems?.replaceChildren();
        if (settingsMemoryEmpty) settingsMemoryEmpty.classList.remove('hidden');
      } catch {
        // silently restore button
      } finally {
        settingsClearMemoryYes.disabled = false;
        settingsClearMemoryYes.textContent = 'Yes, Delete';
      }
    });
  }

  // Settings: Preferences tab
  function initPreferences() {
    const app = document.querySelector('.app-container');
    if (!app) return;

    const savedPrefs = {};
    try {
      const raw = app.dataset.userSettings || '{}';
      Object.assign(savedPrefs, JSON.parse(raw));
    } catch {}

    prefToggles.forEach((btn) => {
      const key = btn.dataset.prefKey || btn.closest('[data-pref-key]')?.dataset.prefKey;
      if (!key) return;
      const val = savedPrefs[key] !== undefined ? savedPrefs[key] : getDefaultPref(key);
      btn.setAttribute('aria-checked', String(val));
      applyPrefEffect(key, val);
      btn.addEventListener('click', async () => {
        if (btn.hasAttribute('disabled')) return;
        const current = btn.getAttribute('aria-checked') === 'true';
        const next = !current;
        btn.setAttribute('aria-checked', String(next));
        applyPrefEffect(key, next);
        await savePref(key, next);
      });
    });

    prefSelects.forEach((sel) => {
      const key = sel.dataset.prefKey;
      const val = savedPrefs[key] !== undefined ? savedPrefs[key] : '';
      if (val) sel.value = val;
      sel.addEventListener('change', async () => {
        await savePref(key, sel.value);
      });
    });
  }

  function getDefaultPref(key) {
    const defaults = {
      show_thinking: false,
      show_token_usage: true,
      show_pipeline_stages: true,
      always_web_search: false,
      compact_messages: false,
    };
    return defaults[key] ?? false;
  }

  function applyPrefEffect(key, value) {
    const app = document.querySelector('.app-container');
    if (!app) return;
    if (key === 'show_token_usage') {
      const usage = document.getElementById('token-usage-card');
      if (usage) usage.style.display = value ? '' : 'none';
    }
    if (key === 'compact_messages') {
      app.classList.toggle('compact-messages', Boolean(value));
    }
    if (key === 'show_pipeline_stages') {
      app.dataset.showPipeline = String(value);
    }
  }

  async function savePref(key, value) {
    try {
      await fetch('/api/user/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify({ key, value }),
      });
    } catch {}
  }

  // Settings: Subscription tab
  let subscriptionDataLoaded = false;

  async function loadSettingsSubscription() {
    if (subscriptionDataLoaded) return;
    const container = document.getElementById('settings-usage-rows');
    if (!container) return;
    try {
      const res = await fetch('/api/billing/subscription', { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error('Could not load subscription');
      const data = await res.json();
      subscriptionDataLoaded = true;

      const { usage, limits } = data;
      container.replaceChildren();

      const rows = [
        { label: 'Daily Tokens', used: usage.tokens_used_day, limit: limits.tokens_day },
        { label: 'Monthly Tokens', used: usage.tokens_used_month, limit: limits.tokens_month },
        { label: '5-Hour Window', used: usage.tokens_used_5h, limit: limits.tokens_5h },
      ];
      rows.forEach(({ label, used, limit }) => {
        const row = element('div', 'usage-row');
        const labelEl = element('span', 'usage-label', label);
        const limitLabel = limit ? (used || 0).toLocaleString() + ' / ' + limit.toLocaleString() : (used || 0).toLocaleString() + ' / ∞';
        const valEl = element('span', 'usage-val', limitLabel);
        row.append(labelEl, valEl);
        container.append(row);
      });

      if (data.subscription && settingsBillingPortalBtn) {
        settingsBillingPortalBtn.classList.remove('hidden');
        settingsBillingPortalBtn.addEventListener('click', async () => {
          settingsBillingPortalBtn.disabled = true;
          settingsBillingPortalBtn.textContent = 'Opening…';
          try {
            const portalRes = await fetch('/api/billing/portal', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
              body: JSON.stringify({ return_url: window.location.href }),
            });
            const portalData = await portalRes.json();
            if (portalData.url) window.location.href = portalData.url;
          } catch {
            settingsBillingPortalBtn.disabled = false;
            settingsBillingPortalBtn.textContent = 'Manage Billing';
          }
        });
      }

      const featuresList = document.getElementById('settings-features-list');
      if (featuresList) {
        featuresList.replaceChildren();
        const features = getPlanFeatures(data.plan);
        features.forEach((f) => {
          const li = element('li', null, f);
          featuresList.append(li);
        });
      }
    } catch {
      if (container) container.textContent = 'Unable to load subscription data.';
    }
  }

  function getPlanFeatures(plan) {
    const featureMap = {
      lite: ['Lite model access', 'Web search enabled', 'RAG knowledge base', '2 file uploads (2 MB each)', '25,000 daily tokens'],
      pro:  ['Core model access', 'Web search enabled', 'RAG knowledge base', 'User memory (cross-session)', '5 file uploads (5 MB each)', '150,000 daily tokens'],
      max:  ['Apex, Core & Lite model access', 'Max-depth reasoning', 'Web search enabled', 'RAG knowledge base', 'User memory (cross-session)', '10 file uploads (10 MB each)', '500,000 daily tokens'],
      developer: ['All models (Apex, Core, Lite)', 'Max-depth reasoning', 'Unlimited token quota', 'User memory (cross-session)', '20 file uploads (20 MB each)', 'Full cloud API tools'],
    };
    return featureMap[plan?.toLowerCase()] || featureMap.lite;
  }

  // Configure Markdown Parser
  if (typeof marked !== 'undefined') {
    marked.setOptions({
      gfm: true,
      breaks: true,
    });
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  function highlightCodeBlocks(containerEl) {
    if (typeof hljs !== 'undefined' && containerEl) {
      containerEl.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    }
  }

  function renderMarkdown(content) {
    if (!content) return '';
    if (typeof marked !== 'undefined') {
      try {
        const rawHtml = marked.parse(content);
        if (typeof DOMPurify !== 'undefined') {
          return DOMPurify.sanitize(rawHtml, {
            ADD_TAGS: ['video', 'audio', 'source'],
            ADD_ATTR: ['target', 'rel', 'controls', 'autoplay', 'loop', 'muted', 'poster', 'playsinline', 'width', 'height'],
          });
        }
        // Fail-closed: escape HTML if DOMPurify is unavailable
        return escapeHtml(rawHtml);
      } catch (err) {
        console.error('Markdown parse error:', err);
      }
    }
    return escapeHtml(content);
  }

  function attachCodeCopyButtons(containerElement) {
    if (!containerElement) return;
    const preBlocks = containerElement.querySelectorAll('pre');
    preBlocks.forEach((pre) => {
      if (pre.dataset.hasCopyBtn === 'true') return;
      pre.dataset.hasCopyBtn = 'true';

      const wrapper = element('div', 'code-block-wrapper');
      pre.parentNode.insertBefore(wrapper, pre);
      wrapper.appendChild(pre);

      const header = element('div', 'code-block-header');
      const codeEl = pre.querySelector('code');
      const langMatch = codeEl ? codeEl.className.match(/language-([a-zA-Z0-9_-]+)/) : null;
      const lang = langMatch ? langMatch[1] : (codeEl?.className || 'code');
      const langSpan = element('span', 'code-lang-label', lang);

      const copyBtn = element('button', 'code-copy-btn');
      copyBtn.type = 'button';
      copyBtn.title = 'Copy code';
      copyBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> <span>Copy</span>';

      copyBtn.addEventListener('click', async () => {
        const codeText = codeEl ? codeEl.innerText : pre.innerText;
        try {
          await navigator.clipboard.writeText(codeText);
          copyBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> <span style="color:#22c55e;">Copied!</span>';
          setTimeout(() => {
            copyBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> <span>Copy</span>';
          }, 2000);
        } catch (e) {
          console.error('Failed to copy code:', e);
        }
      });

      header.append(langSpan, copyBtn);
      wrapper.insertBefore(header, pre);
    });
  }

  function appendMessage(kind, text = '', attachments = null, messageId = null) {
    document.getElementById('chat-empty-state')?.remove();
    const message = element('div', `message ${kind}`);
    if (messageId) {
      message.dataset.messageId = messageId;
    }
    message.dataset.rawText = text;

    const content = element('div', 'message-content');

    if (kind === 'assistant') {
      content.innerHTML = renderMarkdown(text);
      highlightCodeBlocks(content);
      attachCodeCopyButtons(content);
      if (window.CloudGPTActions) {
        window.CloudGPTActions.enhanceCodeBlocksAndTables(content);
      }
      message.append(content);

      if (window.CloudGPTActions && text.trim()) {
        const actionsBar = window.CloudGPTActions.buildAssistantActionBar();
        message.append(actionsBar);
      }
    } else {
      if (attachments && attachments.length && window.CloudGPTAttachments) {
        window.CloudGPTAttachments.renderAttachmentChips(attachments, content);
      }
      const textDiv = element('div', 'msg-text');
      textDiv.textContent = text;
      content.append(textDiv);
      message.append(content);

      if (window.CloudGPTActions) {
        const actionsBar = window.CloudGPTActions.buildUserActionBar();
        message.append(actionsBar);
      }
    }

    container.append(message);
    scrollToBottom();
    return content;
  }

  function appendTyping() {
    document.getElementById('chat-empty-state')?.remove();
    removeTyping();
    const message = element('div', 'message assistant');
    message.id = 'typing-indicator';
    const content = element('div', 'message-content');
    const dotsContainer = element('div', 'typing-dots');
    dotsContainer.setAttribute('aria-label', 'Thinking...');
    const dot1 = element('span', 'typing-dot');
    const dot2 = element('span', 'typing-dot');
    const dot3 = element('span', 'typing-dot');
    dotsContainer.append(dot1, dot2, dot3);
    content.append(dotsContainer);
    message.append(content);
    container.append(message);
    scrollToBottom();
  }

  // ── Pipeline Stage Progress (ChatGPT/Kilo-style) ──────────────────────────
  let stagePanelEl = null;
  let stageRowEl = null;
  let stageTimerInterval = null;
  let stageStartedAt = null;

  // Single spinner primitive (accent-colored) shared by the pipeline stage
  // panel and the thinking panel header.
  function buildSpinnerSvg() {
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '14');
    svg.setAttribute('height', '14');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2.5');
    const circle = document.createElementNS(svgNS, 'circle');
    circle.setAttribute('cx', '12');
    circle.setAttribute('cy', '12');
    circle.setAttribute('r', '10');
    circle.setAttribute('stroke-opacity', '0.25');
    const arc = document.createElementNS(svgNS, 'path');
    arc.setAttribute('d', 'M12 2a10 10 0 0 1 10 10');
    svg.append(circle, arc);
    return svg;
  }

  function createStagePanelIfNeeded() {
    if (stagePanelEl) return;    removeTyping();   // dismiss the 3-dot bounce

    const message = element('div', 'message assistant');
    const content = element('div', 'message-content');
    const panel = element('div', 'pipeline-status-panel');
    const row = element('div', 'pipeline-stage-row');

    // Animated Spinner Icon
    const spinner = element('span', 'pipeline-stage-icon');
    spinner.append(buildSpinnerSvg());

    const nameEl = element('span', 'pipeline-stage-name', 'Preparing...');
    const timeEl = element('span', 'pipeline-stage-time', '0.0s');

    row.append(spinner, nameEl, timeEl);
    panel.append(row);
    content.append(panel);
    message.append(content);
    container.append(message);
    scrollToBottom();

    stagePanelEl = panel;
    stageRowEl = { nameEl, timeEl, spinner };
    stageStartedAt = performance.now();

    stageTimerInterval = setInterval(() => {
      if (stageStartedAt && stageRowEl) {
        const s = ((performance.now() - stageStartedAt) / 1000).toFixed(1);
        stageRowEl.timeEl.textContent = s + 's';
      }
    }, 100);
  }

  function updateStagePanel(label, isComplete = false) {
    createStagePanelIfNeeded();
    if (!stageRowEl) return;

    stageRowEl.nameEl.textContent = label;
    stageStartedAt = performance.now();
    stageRowEl.timeEl.textContent = '0.0s';

    if (isComplete) {
      stageRowEl.spinner.textContent = '✓';
      stageRowEl.spinner.className = 'pipeline-stage-icon stage-complete';
    } else {
      stageRowEl.spinner.className = 'pipeline-stage-icon';
    }
  }

  function removeStagePanelSmooth() {
    if (!stagePanelEl) return;
    if (stageTimerInterval) {
      clearInterval(stageTimerInterval);
      stageTimerInterval = null;
    }
    const elToRemove = stagePanelEl.closest('.message');
    stagePanelEl.classList.add('stage-panel-fade-out');
    setTimeout(() => {
      elToRemove?.remove();
    }, 280);
    stagePanelEl = null;
    stageRowEl = null;
    stageStartedAt = null;
  }

  function updateThinkingStatus(statusText) {
    updateStagePanel(statusText);
  }

  function removeTyping() { document.getElementById('typing-indicator')?.remove(); }

  // Renders an SSE `error` payload into an assistant bubble. Quota rejections
  // (data.quota === true) get a styled notice instead of a plain error bubble;
  // everything else keeps the existing plain-text rendering.
  function showErrorBubble(target, data) {
    if (!target) return;
    if (data.quota === true) {
      target.innerHTML = '';
      const notice = document.createElement('div');
      notice.className = 'quota-notice';
      notice.setAttribute('role', 'alert');
      const icon = document.createElement('span');
      icon.className = 'quota-notice-icon';
      icon.textContent = '⚠';
      const body = document.createElement('div');
      body.className = 'quota-notice-body';
      const title = document.createElement('strong');
      title.className = 'quota-notice-title';
      title.textContent = data.error || 'Gemini Free Tier Quota has Reached its Limit.';
      const detail = document.createElement('span');
      detail.className = 'quota-notice-detail';
      detail.textContent =
        'This is a temporary limit on the AI provider, not a problem with your plan. ' +
        'Quota windows usually reset within a minute — try again shortly.';
      body.append(title, detail);
      notice.append(icon, body);
      target.appendChild(notice);
    } else {
      target.textContent = data.error;
    }
  }

  // ── Thinking Panel (ChatGPT-style collapsible reasoning) ──
  const THINKING_PHASES = [
    'Analysing the question',
    'Gathering context',
    'Reasoning through options',
    'Evaluating trade-offs',
    'Checking the work',
    'Forming the answer',
  ];
  let thinkingPhaseIdx = 0;

  let thinkingPanel = null;
  let thinkingContent = null;
  let thinkingHeaderLabel = null;
  let thinkingTimer = null;
  let thinkingStartedAt = null;
  let thinkingBuffer = '';
  let thinkingRenderPending = false;
  let thinkingHasScrolled = false;

  function createThinkingPanel() {
    thinkingHasScrolled = false;
    const message = element('div', 'message assistant');
    const content = element('div', 'message-content');

    const panel = element('details', 'thinking-panel');
    panel.open = true;

    const summary = element('summary', 'thinking-summary');
    const spinnerIcon = element('span', 'thinking-header-icon');
    spinnerIcon.append(buildSpinnerSvg());
    const label = element('span', 'thinking-label', 'Thinking...');

    const copyThinkBtn = element('button', 'thinking-copy-btn');
    copyThinkBtn.type = 'button';
    copyThinkBtn.title = 'Copy reasoning to clipboard';
    copyThinkBtn.setAttribute('aria-label', 'Copy reasoning to clipboard');
    copyThinkBtn.textContent = '⎘';
    copyThinkBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();    // prevent <details> toggle
      const text = thinkingBuffer || thinkingContent?.innerText || '';
      try {
        await navigator.clipboard.writeText(text);
        copyThinkBtn.textContent = '✓';
        setTimeout(() => { copyThinkBtn.textContent = '⎘'; }, 1800);
      } catch (_) {
        // Clipboard API unavailable — silent fail
      }
    });

    summary.append(spinnerIcon, label, copyThinkBtn);

    const body = element('div', 'thinking-body');
    const textEl = element('div', 'thinking-text');

    body.append(textEl);
    panel.append(summary, body);
    message.append(content);
    content.append(panel);
    container.append(message);
    scrollToBottom();

    thinkingPanel = panel;
    thinkingContent = textEl;
    thinkingHeaderLabel = label;
    thinkingStartedAt = performance.now();
    thinkingTimer = setInterval(() => {
      if (!thinkingStartedAt || !thinkingHeaderLabel) return;
      const elapsed = (performance.now() - thinkingStartedAt) / 1000;
      // Advance phase every 3.5 s, capping at last phase
      thinkingPhaseIdx = Math.min(
        Math.floor(elapsed / 3.5),
        THINKING_PHASES.length - 1
      );
      thinkingHeaderLabel.textContent =
        `${THINKING_PHASES[thinkingPhaseIdx]}... ${elapsed.toFixed(1)}s`;
    }, 100);
    return textEl;
  }

  function finishThinkingPanel(elapsedSeconds) {
    if (thinkingTimer) {
      clearInterval(thinkingTimer);
      thinkingTimer = null;
    }
    if (thinkingContent && thinkingBuffer) {
      thinkingContent.innerHTML = renderMarkdown(thinkingBuffer);
      highlightCodeBlocks(thinkingContent);
      attachCodeCopyButtons(thinkingContent);
    }
    if (thinkingHeaderLabel) {
      const shown = typeof elapsedSeconds === 'number' && elapsedSeconds > 0
        ? elapsedSeconds
        : (thinkingStartedAt ? (performance.now() - thinkingStartedAt) / 1000 : 0);
      thinkingHeaderLabel.textContent = shown > 0 ? `Thought for ${shown.toFixed(1)}s` : 'Thought briefly';
    }
    const spinnerEl = thinkingPanel?.querySelector('.thinking-header-icon');
    if (spinnerEl) {
      spinnerEl.textContent = '✓';
      spinnerEl.classList.add('thinking-header-complete');
    }
    if (thinkingPanel) thinkingPanel.classList.add('thinking-complete');
    // panel.open stays true — user collapses manually
    scrollToBottom();
    thinkingStartedAt = null;
  }

  function scheduleThinkingRender() {
    if (thinkingRenderPending || !thinkingContent) return;
    thinkingRenderPending = true;
    setTimeout(() => {
      if (thinkingContent && thinkingBuffer) {
        thinkingContent.innerHTML = renderMarkdown(thinkingBuffer);
        highlightCodeBlocks(thinkingContent);
        if (!thinkingHasScrolled) {
          scrollToBottom();
          thinkingHasScrolled = true;
        }
      }
      thinkingRenderPending = false;
    }, 120);  // 120 ms throttle — smooth but not too frequent
  }

  function resetThinkingPanelState() {
    if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
    removeStagePanelSmooth();
    thinkingPanel = null;
    thinkingContent = null;
    thinkingHeaderLabel = null;
    thinkingStartedAt = null;
    thinkingBuffer = '';
    thinkingRenderPending = false;
    thinkingPhaseIdx = 0;
    thinkingHasScrolled = false;
  }

  function appendUsageSummary(target, usage, thinking) {
    if (!usage) return;
    const parts = [];
    if (typeof usage.total_consumed === 'number') parts.push(`${usage.total_consumed.toLocaleString()} tokens used`);
    if (typeof usage.thinking === 'number' && usage.thinking > 0 && thinking?.level) parts.push(`${thinking.level} thinking: ${usage.thinking.toLocaleString()} tokens`);
    if (parts.length === 0) return;
    const bar = element('div', 'usage-summary');
    bar.append(element('span', 'usage-summary-text', parts.join(' · ')));
    target.append(bar);
  }

  function appendThinkingBadge(target, thinking) {
    if (!thinking || !thinking.level) return;
    const badge = element('div', 'thinking-level-badge');
    const levelLower = thinking.level.toLowerCase();
    badge.classList.add(`thinking-level-${levelLower}`);

    const iconSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    iconSvg.setAttribute('viewBox', '0 0 24 24');
    iconSvg.setAttribute('width', '13');
    iconSvg.setAttribute('height', '13');
    iconSvg.setAttribute('fill', 'none');
    iconSvg.setAttribute('stroke', 'currentColor');
    iconSvg.setAttribute('stroke-width', '2');
    iconSvg.setAttribute('stroke-linecap', 'round');
    iconSvg.setAttribute('stroke-linejoin', 'round');
    const bulbPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    bulbPath.setAttribute('d', 'M15 14c.2-1 .7-1.7 1.5-2.5 1-1 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5');
    const neckLine = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    neckLine.setAttribute('d', 'M9 18h6');
    const baseLine = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    baseLine.setAttribute('d', 'M10 22h4');
    iconSvg.append(bulbPath, neckLine, baseLine);
    badge.append(iconSvg);

    let labelText = `${thinking.level} thinking`;
    if (typeof thinking.thinking_tokens === 'number' && thinking.thinking_tokens > 0) {
      labelText += ` · ${thinking.thinking_tokens.toLocaleString()} tokens`;
    }
    if (typeof thinking.elapsed === 'number' && thinking.elapsed > 0) {
      labelText += ` · ${thinking.elapsed.toFixed(1)}s`;
    }
    badge.append(element('span', 'thinking-badge-text', labelText));
    target.append(badge);
  }

  function safeUrl(value) {
    try {
      const url = new URL(value, window.location.origin);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  }

  function faviconUrl(url) {
    try {
      const host = new URL(url).hostname;
      return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=16`;
    } catch (_) { return null; }
  }

  function formatRouteName(route) {
    const normalized = String(route).toUpperCase();
    if (normalized === 'INTERNET') return 'Internet';
    if (normalized === 'RAG') return 'RAG';
    if (normalized === 'CALCULATOR') return 'Calculator';
    if (normalized === 'PRICING') return 'Pricing';
    if (normalized === 'CLOUD_API') return 'Cloud API';
    return String(route).charAt(0).toUpperCase() + String(route).slice(1).toLowerCase();
  }

  function buildWebSourceCard(source) {
    const url = safeUrl(source.url);
    if (!url) return null;

    let domain = '';
    try {
      domain = new URL(url).hostname.replace(/^www\./, '');
    } catch (_) {
      domain = source.domain || 'web';
    }

    const card = element('div', 'web-source-card');

    const topRow = element('div', 'web-source-top');
    const domainPill = element('div', 'web-source-domain-pill');

    const fav = faviconUrl(url);
    if (fav) {
      const img = document.createElement('img');
      img.src = fav;
      img.width = 13;
      img.height = 13;
      img.alt = '';
      img.className = 'web-source-fav';
      img.onerror = () => img.remove();
      domainPill.append(img);
    }
    const domainText = element('span', 'web-source-domain-text', domain);
    domainPill.append(domainText);

    const copyBtn = element('button', 'web-source-copy-btn');
    copyBtn.type = 'button';
    copyBtn.title = 'Copy link';
    copyBtn.setAttribute('aria-label', 'Copy link');
    copyBtn.innerHTML = `
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
      </svg>
    `;
    copyBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      navigator.clipboard.writeText(url).then(() => {
        copyBtn.classList.add('copied');
        copyBtn.innerHTML = `
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        `;
        setTimeout(() => {
          copyBtn.classList.remove('copied');
          copyBtn.innerHTML = `
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
          `;
        }, 1500);
      }).catch(() => {});
    });

    topRow.append(domainPill, copyBtn);

    const titleLink = element('a', 'web-source-title-link');
    titleLink.href = url;
    titleLink.target = '_blank';
    titleLink.rel = 'noopener noreferrer';
    titleLink.title = source.title || url;

    titleLink.innerHTML = `
      <span class="web-source-title-text">${source.title || domain}</span>
      <svg class="web-source-ext-icon" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
        <polyline points="15 3 21 3 21 9"></polyline>
        <line x1="10" y1="14" x2="21" y2="3"></line>
      </svg>
    `;

    card.append(topRow, titleLink);
    return card;
  }

  function addCitations(target, data) {
    const allSources = data.sources || [];
    // Strictly display external internet links — exclude all RAG / internal corpus references
    const internetSources = allSources.filter((s) => {
      const isRag = (s.source_type || '').toLowerCase() === 'rag';
      if (isRag) return false;
      const url = safeUrl(s.url);
      return Boolean(url && (url.startsWith('http://') || url.startsWith('https://')));
    });

    // De-duplicate by URL
    const seenUrls = new Set();
    const uniqueSources = [];
    for (const src of internetSources) {
      const norm = src.url.toLowerCase().replace(/\/$/, '');
      if (!seenUrls.has(norm)) {
        seenUrls.add(norm);
        uniqueSources.push(src);
      }
    }

    // Do NOT show RAG, and do NOT show panel if no valid internet links exist
    if (uniqueSources.length === 0) return;

    const container = element('div', 'web-sources-container');

    const header = element('div', 'web-sources-header');
    header.innerHTML = `
      <div class="web-sources-title">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="2" y1="12" x2="22" y2="12"></line>
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
        </svg>
        <span>Web Sources</span>
        <span class="web-sources-count">${uniqueSources.length}</span>
      </div>
    `;

    const grid = element('div', 'web-sources-grid');
    uniqueSources.forEach((src) => {
      const card = buildWebSourceCard(src);
      if (card) grid.append(card);
    });

    container.append(header, grid);
    target.append(container);
  }

  function highlightMatches(containerNode, text, query) {
    if (!query || !query.trim()) {
      containerNode.textContent = text;
      return;
    }
    const q = query.trim().toLowerCase();
    const lowerText = text.toLowerCase();
    let startIndex = 0;
    let matchIndex = lowerText.indexOf(q, startIndex);

    if (matchIndex === -1) {
      containerNode.textContent = text;
      return;
    }

    containerNode.replaceChildren();
    while (matchIndex !== -1) {
      if (matchIndex > startIndex) {
        containerNode.append(document.createTextNode(text.substring(startIndex, matchIndex)));
      }
      const mark = element('mark', 'search-highlight', text.substring(matchIndex, matchIndex + q.length));
      containerNode.append(mark);
      startIndex = matchIndex + q.length;
      matchIndex = lowerText.indexOf(q, startIndex);
    }
    if (startIndex < text.length) {
      containerNode.append(document.createTextNode(text.substring(startIndex)));
    }
  }

  function renderHistory(items, searchQuery = currentSearchQuery) {
    if (!historyList) return;
    historyList.replaceChildren();

    const query = (searchQuery || '').trim().toLowerCase();
    const filtered = query
      ? items.filter((s) => (s.title || '').toLowerCase().includes(query))
      : items;

    if (filtered.length === 0) {
      return;
    }

    filtered.forEach((session) => {
      const item = element('li', 'history-item');
      if (session.session_id === sessionId) item.classList.add('active');
      if (session.isLoading) item.classList.add('loading-session');
      item.dataset.sessionId = session.session_id;

      const textContainer = element('div', 'history-text-container');
      const titleSpan = element('span', 'history-title');

      if (session.isLoading) {
        titleSpan.classList.add('loading-shimmer');
        titleSpan.innerHTML = '<span class="history-spinner"></span>Generating title...';
      } else {
        const rawTitle = session.title || 'New conversation';
        titleSpan.title = rawTitle;
        highlightMatches(titleSpan, rawTitle, query);
      }

      const date = session.created_at ? new Date(session.created_at) : null;
      const timeSpan = element('span', 'history-time', session.isLoading ? 'Just now' : (date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString() : ''));
      textContainer.append(titleSpan, timeSpan);

      const deleteBtn = element('button', 'delete-session-btn');
      deleteBtn.type = 'button';
      deleteBtn.title = 'Delete conversation';
      deleteBtn.setAttribute('aria-label', 'Delete conversation');
      deleteBtn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>';

      deleteBtn.addEventListener('click', async (event) => {
        event.stopPropagation();
        await deleteSession(session.session_id);
      });

      if (session.isLoading) {
        deleteBtn.style.display = 'none';
      }

      item.append(textContainer, deleteBtn);
      item.addEventListener('click', () => {
        if (session.isLoading) return;
        closeMobileSidebar();
        loadSession(session);
      });
      historyList.append(item);
    });
  }

  async function deleteSession(targetSessionId) {
    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(targetSessionId)}`, {
        method: 'DELETE',
        headers: { 'X-CSRF-Token': csrfToken }
      });
      if (!response.ok) throw new Error('Failed to delete conversation');
      sessions = sessions.filter((s) => s.session_id !== targetSessionId);
      if (sessionId === targetSessionId) {
        sessionId = generateUUID();
        isNew = true;
        container.replaceChildren();
        renderStarterCards();
        title.textContent = 'New Conversation';
      }
      renderHistory(sessions, currentSearchQuery);
    } catch (error) {
      console.error('Error deleting session:', error);
    }
  }

  async function refreshSessions() {
    try {
      const response = await fetch('/api/sessions');
      if (!response.ok) throw new Error('Unable to load chat history');
      sessions = await response.json();
      renderHistory(sessions, currentSearchQuery);
    } catch (err) {
      console.error('Error fetching sessions:', err);
    }
  }

  async function loadSession(session) {
    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(session.session_id)}/messages`);
      if (!response.ok) throw new Error('Unable to load conversation');
      const messages = await response.json();
      sessionId = session.session_id;
      isNew = false;
      container.replaceChildren();
      title.textContent = session.title || 'New Conversation';
      if (!messages || messages.length === 0) {
        renderStarterCards();
      } else {
        messages.forEach((message) => {
          let atts = message.attachments;
          if (typeof atts === 'string') {
            try { atts = JSON.parse(atts); } catch (_) { atts = null; }
          }
          appendMessage(
            message.role === 'user' ? 'user' : 'assistant',
            message.content || '',
            atts || null,
            message.id || null
          );
        });
      }
      renderHistory(sessions, currentSearchQuery);
      scrollToBottom();
    } catch (error) { console.error('Failed to load session messages:', error); }
  }

  function updateUsage(usage) {
    if (!usage) return;
    const valDay = document.getElementById('usage-val-day');
    const valMonth = document.getElementById('usage-val-month');
    const progressDay = document.getElementById('usage-progress-day');
    const progressMonth = document.getElementById('usage-progress-month');

    if (valDay) {
      if (usage.limit_day === 'Unlimited' || usage.is_unlimited) {
        valDay.textContent = `${(usage.tokens_used_day || 0).toLocaleString()} / ∞`;
      } else if (usage.limit_day) {
        valDay.textContent = `${(usage.tokens_used_day || 0).toLocaleString()} / ${usage.limit_day.toLocaleString()}`;
      }
    }
    if (progressDay) {
      if (usage.limit_day === 'Unlimited' || usage.is_unlimited) {
        progressDay.style.width = '0%';
      } else if (usage.limit_day && usage.limit_day > 0) {
        const pct = Math.min(Math.max((usage.tokens_used_day / usage.limit_day) * 100, 0), 100);
        progressDay.style.width = `${pct.toFixed(1)}%`;
      }
    }

    if (valMonth) {
      if (usage.limit_month === 'Unlimited' || usage.is_unlimited) {
        valMonth.textContent = `${(usage.tokens_used_month || 0).toLocaleString()} / ∞`;
      } else if (usage.limit_month) {
        valMonth.textContent = `${(usage.tokens_used_month || 0).toLocaleString()} / ${usage.limit_month.toLocaleString()}`;
      }
    }
    if (progressMonth) {
      if (usage.limit_month === 'Unlimited' || usage.is_unlimited) {
        progressMonth.style.width = '0%';
      } else if (usage.limit_month && usage.limit_month > 0) {
        const pct = Math.min(Math.max((usage.tokens_used_month / usage.limit_month) * 100, 0), 100);
        progressMonth.style.width = `${pct.toFixed(1)}%`;
      }
    }
  }

  function parseSseBlock(block) {
    const lines = block.split('\n');
    const data = lines.find((line) => line.startsWith('data:'));
    if (!data) return null;
    try { return JSON.parse(data.slice(5).trim()); } catch (_) { return null; }
  }

  async function sendMessage() {
    const text = input.value.trim();
    const filesToSend = [...stagedFiles];
    if ((!text && filesToSend.length === 0) || activeController) return;

    const wasNew = isNew;
    isNew = false;
    input.value = '';
    input.style.height = 'auto';
    stagedFiles = [];
    renderFileChips();
    send.disabled = true;

    let uploadedAttachments = [];
    if (filesToSend.length > 0 && window.CloudGPTAttachments) {
      try {
        for (const file of filesToSend) {
          const res = await window.CloudGPTAttachments.uploadSingleFile(file, csrfToken);
          uploadedAttachments.push({
            attachment_id: res.attachment_id,
            filename: res.filename,
            size: res.size,
            content_type: res.content_type,
            kind: res.kind,
            previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : null,
          });
        }
      } catch (uploadErr) {
        alert(uploadErr.message || 'Failed to upload attachment.');
        send.disabled = false;
        return;
      }
    }

    const userContent = appendMessage('user', text, uploadedAttachments);
    const userMsgEl = userContent ? userContent.closest('.message') : null;
    appendTyping();
    activeController = new AbortController();
    let answer = '';
    let assistant = null;
    let thinkingWasStreamed = false;

    // During output generation, show this session in sidebar with loading state
    if (wasNew || !sessions.some((s) => s.session_id === sessionId)) {
      const loadingSession = {
        session_id: sessionId,
        title: 'New conversation',
        created_at: new Date().toISOString(),
        isLoading: true,
      };
      sessions = [loadingSession, ...sessions.filter((s) => s.session_id !== sessionId)];
      renderHistory(sessions, currentSearchQuery);
    }

    try {
      const attachmentsPayload = uploadedAttachments.map((f) => ({
        attachment_id: f.attachment_id,
        name: f.filename,
        filename: f.filename,
        size: f.size,
        type: f.content_type,
        kind: f.kind,
      }));

      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        signal: activeController.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify({
          query: text || `Please analyze the attached files: ${uploadedAttachments.map((f) => f.filename).join(', ')}`,
          session_id: sessionId,
          mode: selectedModel,
          thinking_level: selectedThinking,
          attachments: attachmentsPayload,
          request_id: generateUUID(),
        }),
      });

      if (!response.ok || !response.body) throw new Error('Chat request failed');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() || '';
        blocks.forEach((block) => {
          const data = parseSseBlock(block);
          if (!data) return;
          if (data.status && !data.stage && !thinkingPanel) {
            updateStagePanel(data.status, false);
          }
          if (data.stage) {
            const isDone = data.status === 'complete';
            updateStagePanel(data.label || data.stage, isDone);
          }
          if (data.type === 'session_title' && data.title) {
            title.textContent = data.title;
            const targetId = data.session_id || sessionId;
            const sess = sessions.find((s) => s.session_id === targetId);
            if (sess) {
              sess.title = data.title;
              sess.isLoading = false;
            } else {
              sessions.unshift({
                session_id: targetId,
                title: data.title,
                created_at: new Date().toISOString(),
                isLoading: false,
              });
            }
            renderHistory(sessions, currentSearchQuery);
          }
          if (data.type === 'memory_updated' && data.key) {
            showMemoryToast(`${data.key}: ${data.value}`);
          }
          if (data.thinking_token) {
            thinkingWasStreamed = true;
            removeTyping();
            removeStagePanelSmooth();
            if (!thinkingContent) createThinkingPanel();
            thinkingBuffer += data.thinking_token;
            scheduleThinkingRender();
          }
          if (data.thinking_done) {
            finishThinkingPanel(data.elapsed);
          }
          if (data.token) {
            removeTyping();
            removeStagePanelSmooth();
            if (thinkingPanel && !thinkingPanel.classList.contains('thinking-complete')) {
              finishThinkingPanel(null);
            }
            if (!assistant) {
              assistant = appendMessage('assistant');
            }
            answer += data.token;
            assistant.innerHTML = renderMarkdown(answer);
            scrollToBottom();
          }
          if (data.error) {
            removeTyping();
            removeStagePanelSmooth();
            if (!assistant) assistant = appendMessage('assistant');
            showErrorBubble(assistant, data);
          }
          if (data.done) {
            removeTyping();
            removeStagePanelSmooth();
            if (thinkingPanel && !thinkingPanel.classList.contains('thinking-complete')) {
              finishThinkingPanel(data.thinking?.elapsed ?? null);
            }
            if (!assistant && answer) assistant = appendMessage('assistant');
            if (assistant) {
              assistant.innerHTML = renderMarkdown(answer);
              highlightCodeBlocks(assistant);
              attachCodeCopyButtons(assistant);
              if (window.CloudGPTActions) {
                window.CloudGPTActions.enhanceCodeBlocksAndTables(assistant);
              }
              addCitations(assistant, data);
              appendThinkingBadge(assistant, data.thinking);
              appendUsageSummary(assistant, data.usage, data.thinking);

              const parentMsg = assistant.closest('.message');
              if (parentMsg) {
                parentMsg.dataset.rawText = answer;
                if (data.message_id) {
                  parentMsg.dataset.messageId = data.message_id;
                }
                if (!parentMsg.querySelector('.msg-actions.assistant-actions') && window.CloudGPTActions) {
                  parentMsg.append(window.CloudGPTActions.buildAssistantActionBar());
                }
              }

              if (data.user_message_id && userMsgEl) {
                userMsgEl.dataset.messageId = data.user_message_id;
              }
            }
            updateUsage(data.usage);
            if (data.title) {
              title.textContent = data.title;
              const targetId = data.session_id || sessionId;
              const sess = sessions.find((s) => s.session_id === targetId);
              if (sess) {
                sess.title = data.title;
                sess.isLoading = false;
                renderHistory(sessions, currentSearchQuery);
              }
            } else {
              const sess = sessions.find((s) => s.session_id === sessionId);
              if (sess && sess.isLoading) {
                sess.isLoading = false;
                renderHistory(sessions, currentSearchQuery);
              }
            }
            if (wasNew) refreshSessions().catch(console.error);
          }
        });
      }
    } catch (error) {
      const currentSess = sessions.find((s) => s.session_id === sessionId);
      if (currentSess && currentSess.isLoading) {
        currentSess.isLoading = false;
        renderHistory(sessions, currentSearchQuery);
      }
      if (error.name !== 'AbortError') {
        removeTyping();
        if (thinkingPanel && !thinkingPanel.classList.contains('thinking-complete')) finishThinkingPanel(null);
        (assistant || appendMessage('assistant')).textContent = 'Sorry, the request could not be completed. Please try again.';
      }
    } finally {
      resetThinkingPanelState();
      activeController = null;
      updateSendState();
    }
  }

  async function triggerRegenerateStream(promptText) {
    if (activeController) return;
    appendTyping();
    activeController = new AbortController();
    let answer = '';
    let assistant = null;
    let thinkingWasStreamed = false;

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        signal: activeController.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify({
          query: promptText,
          session_id: sessionId,
          mode: selectedModel,
          thinking_level: selectedThinking,
          request_id: generateUUID(),
        }),
      });

      if (!response.ok || !response.body) throw new Error('Regenerate request failed');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() || '';
        blocks.forEach((block) => {
          const data = parseSseBlock(block);
          if (!data) return;
          if (data.status && !data.stage && !thinkingPanel) {
            updateStagePanel(data.status, false);
          }
          if (data.stage) {
            const isDone = data.status === 'complete';
            updateStagePanel(data.label || data.stage, isDone);
          }
          if (data.thinking_token) {
            thinkingWasStreamed = true;
            removeTyping();
            removeStagePanelSmooth();
            if (!thinkingContent) createThinkingPanel();
            thinkingBuffer += data.thinking_token;
            scheduleThinkingRender();
          }
          if (data.thinking_done) {
            finishThinkingPanel(data.elapsed);
          }
          if (data.token) {
            removeTyping();
            removeStagePanelSmooth();
            if (thinkingPanel && !thinkingPanel.classList.contains('thinking-complete')) {
              finishThinkingPanel(null);
            }
            if (!assistant) {
              assistant = appendMessage('assistant');
            }
            answer += data.token;
            assistant.innerHTML = renderMarkdown(answer);
            scrollToBottom();
          }
          if (data.error) {
            removeTyping();
            removeStagePanelSmooth();
            if (!assistant) assistant = appendMessage('assistant');
            showErrorBubble(assistant, data);
          }
          if (data.done) {
            removeTyping();
            removeStagePanelSmooth();
            if (thinkingPanel && !thinkingPanel.classList.contains('thinking-complete')) {
              finishThinkingPanel(data.thinking?.elapsed ?? null);
            }
            if (!assistant && answer) assistant = appendMessage('assistant');
            if (assistant) {
              assistant.innerHTML = renderMarkdown(answer);
              highlightCodeBlocks(assistant);
              attachCodeCopyButtons(assistant);
              if (window.CloudGPTActions) {
                window.CloudGPTActions.enhanceCodeBlocksAndTables(assistant);
              }
              addCitations(assistant, data);
              appendThinkingBadge(assistant, data.thinking);
              appendUsageSummary(assistant, data.usage, data.thinking);

              const parentMsg = assistant.closest('.message');
              if (parentMsg) {
                parentMsg.dataset.rawText = answer;
                if (data.message_id) {
                  parentMsg.dataset.messageId = data.message_id;
                }
                if (!parentMsg.querySelector('.msg-actions.assistant-actions') && window.CloudGPTActions) {
                  parentMsg.append(window.CloudGPTActions.buildAssistantActionBar());
                }
              }
            }
            updateUsage(data.usage);
          }
        });
      }
    } catch (error) {
      if (error.name !== 'AbortError') {
        removeTyping();
        if (thinkingPanel && !thinkingPanel.classList.contains('thinking-complete')) finishThinkingPanel(null);
        (assistant || appendMessage('assistant')).textContent = 'Sorry, the request could not be completed. Please try again.';
      }
    } finally {
      resetThinkingPanelState();
      activeController = null;
      updateSendState();
    }
  }

  window.CloudGPTChat = {
    async undoLastMessage(messageEl, messageId) {
      if (activeController) return;
      if (messageId) {
        try {
          await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/truncate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ message_id: parseInt(messageId, 10) }),
          });
        } catch (e) {
          console.error('Failed to truncate session:', e);
        }
      }
      const rawText = messageEl.dataset.rawText || '';
      input.value = rawText;
      input.style.height = 'auto';
      input.style.height = `${input.scrollHeight}px`;
      updateSendState();
      input.focus();

      let next = messageEl.nextElementSibling;
      while (next) {
        const toRemove = next;
        next = next.nextElementSibling;
        toRemove.remove();
      }
      messageEl.remove();
    },

    async resendFromMessage(messageEl, messageId, newText) {
      if (activeController) return;
      if (messageId) {
        try {
          await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/truncate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ message_id: parseInt(messageId, 10) }),
          });
        } catch (e) {
          console.error('Failed to truncate session:', e);
        }
      }
      let next = messageEl.nextElementSibling;
      while (next) {
        const toRemove = next;
        next = next.nextElementSibling;
        toRemove.remove();
      }
      messageEl.remove();

      input.value = newText;
      sendMessage();
    },

    async regenerateFromMessage(assistantMsgEl, messageId) {
      if (activeController) return;
      let prevUserEl = assistantMsgEl.previousElementSibling;
      while (prevUserEl && !prevUserEl.classList.contains('user')) {
        prevUserEl = prevUserEl.previousElementSibling;
      }
      if (!prevUserEl) return;

      const userPrompt = prevUserEl.dataset.rawText || '';
      const truncateId = assistantMsgEl.dataset.messageId || prevUserEl.dataset.messageId;

      if (truncateId) {
        try {
          await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/truncate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ message_id: parseInt(truncateId, 10) }),
          });
        } catch (e) {
          console.error('Failed to truncate session for regenerate:', e);
        }
      }

      let next = assistantMsgEl.nextElementSibling;
      while (next) {
        const toRemove = next;
        next = next.nextElementSibling;
        toRemove.remove();
      }
      assistantMsgEl.remove();

      triggerRegenerateStream(userPrompt);
    },
  };


  newChat.addEventListener('click', () => {
    closeMobileSidebar();
    activeController?.abort();
    sessionId = generateUUID();
    isNew = true;
    stagedFiles = [];
    renderFileChips();
    container.replaceChildren();
    renderStarterCards();
    title.textContent = 'New Conversation';

    // Deselect any active session in the sidebar
    renderHistory(sessions, currentSearchQuery);
    input.focus();
  });

  // ── History Search Event Listeners ──
  if (historySearchInput) {
    historySearchInput.addEventListener('input', () => {
      currentSearchQuery = historySearchInput.value.trim();
      if (historySearchClear) {
        historySearchClear.classList.toggle('hidden', !currentSearchQuery);
      }
      renderHistory(sessions, currentSearchQuery);
    });

    historySearchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        historySearchInput.value = '';
        currentSearchQuery = '';
        if (historySearchClear) historySearchClear.classList.add('hidden');
        renderHistory(sessions, '');
        historySearchInput.blur();
      }
    });
  }

  function clearHistorySearch() {
    if (historySearchInput) {
      historySearchInput.value = '';
      currentSearchQuery = '';
      if (historySearchClear) historySearchClear.classList.add('hidden');
      renderHistory(sessions, '');
      historySearchInput.focus();
    }
  }

  if (historySearchClear) {
    historySearchClear.addEventListener('click', clearHistorySearch);
  }

  // Global Keyboard Shortcuts: Ctrl+K / Cmd+K or "/" to focus history search
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (historySearchInput) {
        historySearchInput.focus();
        historySearchInput.select();
      }
    } else if (e.key === '/' && document.activeElement !== input && document.activeElement !== historySearchInput && !document.activeElement?.isContentEditable && document.activeElement?.tagName !== 'TEXTAREA' && document.activeElement?.tagName !== 'INPUT') {
      e.preventDefault();
      historySearchInput?.focus();
    }
  });

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${input.scrollHeight}px`;
    updateSendState();
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  const inputCard = document.querySelector('.input-card');
  if (inputCard && window.CloudGPTAttachments) {
    window.CloudGPTAttachments.setupDragAndDrop(inputCard, (files) => {
      files.forEach((f) => stagedFiles.push(f));
      renderFileChips();
    });
  }

  if (window.CloudGPTAttachments) {
    window.CloudGPTAttachments.setupClipboardPaste(input, (files) => {
      files.forEach((f) => stagedFiles.push(f));
      renderFileChips();
    });
  }

  if (window.CloudGPTActions) {
    window.CloudGPTActions.initActionsDelegation(container);
  }

  send.addEventListener('click', sendMessage);

  // Initialize mobile drawer and starter cards
  mobileSidebarToggle?.addEventListener('click', () => {
    if (sidebar?.classList.contains('open')) {
      closeMobileSidebar();
    } else {
      openMobileSidebar();
    }
  });
  sidebarOverlay?.addEventListener('click', closeMobileSidebar);
  initStarterCards();
  initPreferences();

  refreshSessions().catch(console.error);
});

