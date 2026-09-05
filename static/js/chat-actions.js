/**
 * CloudGPT — Message Actions & Artifacts Handler
 * Action toolbars for user and assistant messages:
 * Copy, Undo, Edit & Resend, Regenerate, Feedback (Like/Dislike), Read Aloud (TTS),
 * Export Markdown/CSV/Code, and Artifact Card Rendering.
 */

(function () {
  'use strict';

  let activeSpeakBtn = null;

  function stripMarkdown(md) {
    if (!md) return '';
    return md
      // Strip markdown code fences (including multiline code)
      .replace(/```[\s\S]*?```/g, ' [code snippet] ')
      // Strip inline code
      .replace(/`([^`]+)`/g, '$1')
      // Strip artifact blocks
      .replace(/:::[\s\S]*?:::/g, ' [document] ')
      // Strip markdown image tags (multiline capable, including base64 URLs)
      .replace(/!\[[\s\S]*?\]\([\s\S]*?\)/g, '')
      // Strip HTML img and svg tags
      .replace(/<img[\s\S]*?>/gi, '')
      .replace(/<svg[\s\S]*?<\/svg>/gi, '')
      // Strip standalone base64 data URIs
      .replace(/data:[a-zA-Z0-9/+-]+;base64,[A-Za-z0-9+/=\s]+/g, '')
      // Strip markdown links [label](url) -> label
      .replace(/\[([\s\S]*?)\]\([\s\S]*?\)/g, '$1')
      // Strip table header delimiters |---|---|
      .replace(/\|[\s-:]+\|/g, ' ')
      // Convert table pipe separators to commas
      .replace(/\|/g, ', ')
      // Headers #
      .replace(/#{1,6}\s+(.*)/g, '$1. ')
      // Bold and italics
      .replace(/(\*\*|__)([\s\S]*?)\1/g, '$2')
      .replace(/(\*|_)([\s\S]*?)\1/g, '$2')
      // Strikethrough
      .replace(/~~([\s\S]*?)~~/g, '$1')
      // Blockquotes
      .replace(/>\s+(.*)/g, '$1')
      // Bullet lists and numbered lists
      .replace(/^[\s]*[-*+]\s+(.*)/gm, '$1. ')
      .replace(/^[\s]*\d+\.\s+(.*)/gm, '$1. ')
      // HTML tags
      .replace(/<[^>]+>/g, ' ')
      // Multiple dots or line breaks
      .replace(/\n+/g, '. ')
      .replace(/\.{2,}/g, '.')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  function chunkTextForSpeech(text, maxChunkLen = 220) {
    if (!text || text.length <= maxChunkLen) return text ? [text] : [];
    const chunks = [];
    let remaining = text;
    while (remaining.length > 0) {
      if (remaining.length <= maxChunkLen) {
        chunks.push(remaining.trim());
        break;
      }
      let splitIdx = -1;
      const match = remaining.slice(0, maxChunkLen).match(/.*?[.!?]\s+|.*?[,;:]\s+/);
      if (match && match[0].length > 40) {
        splitIdx = match[0].length;
      } else {
        const lastSpace = remaining.lastIndexOf(' ', maxChunkLen);
        splitIdx = lastSpace > 40 ? lastSpace : maxChunkLen;
      }
      chunks.push(remaining.slice(0, splitIdx).trim());
      remaining = remaining.slice(splitIdx).trim();
    }
    return chunks.filter(Boolean);
  }

  function stopSpeaking() {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    if (window._ttsKeepAliveTimer) {
      clearInterval(window._ttsKeepAliveTimer);
      window._ttsKeepAliveTimer = null;
    }
    window._activeSpeechUtterance = null;
    if (activeSpeakBtn) {
      activeSpeakBtn.classList.remove('active');
      activeSpeakBtn.innerHTML = `
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
          <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>
        </svg>
        <span>Read aloud</span>
      `;
      activeSpeakBtn = null;
    }
  }

  function toggleReadAloud(text, btn) {
    if (!('speechSynthesis' in window)) {
      alert('Text-to-speech is not supported in this browser.');
      return;
    }

    if (activeSpeakBtn === btn) {
      stopSpeaking();
      return;
    }

    stopSpeaking();

    const plainText = stripMarkdown(text);
    if (!plainText) return;

    const chunks = chunkTextForSpeech(plainText);
    if (chunks.length === 0) return;

    btn.classList.add('active');
    btn.innerHTML = `
      <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="6" y="4" width="4" height="16"></rect>
        <rect x="14" y="4" width="4" height="16"></rect>
      </svg>
      <span>Stop</span>
    `;
    activeSpeakBtn = btn;

    let chunkIndex = 0;

    function speakNext() {
      if (chunkIndex >= chunks.length || activeSpeakBtn !== btn) {
        stopSpeaking();
        return;
      }

      try {
        window.speechSynthesis.resume();
      } catch (_) {}

      const utterance = new SpeechSynthesisUtterance(chunks[chunkIndex]);
      utterance.rate = 1.0;
      utterance.pitch = 1.0;

      // Select natural English voice if available
      try {
        const voices = window.speechSynthesis.getVoices();
        if (voices && voices.length > 0) {
          const preferred = voices.find(v => v.lang.startsWith('en') && (v.name.includes('Natural') || v.name.includes('Google') || v.name.includes('Online')));
          if (preferred) utterance.voice = preferred;
        }
      } catch (_) {}

      // Pin to window to avoid Chrome premature garbage collection
      window._activeSpeechUtterance = utterance;

      utterance.onend = () => {
        chunkIndex++;
        speakNext();
      };

      utterance.onerror = (event) => {
        if (event.error === 'interrupted' || event.error === 'canceled') {
          return; // Normal pause/cancel
        }
        console.warn('Speech synthesis notice:', event.error);
        stopSpeaking();
      };

      try {
        window.speechSynthesis.speak(utterance);
      } catch (err) {
        console.warn('speechSynthesis.speak failed:', err);
        stopSpeaking();
      }
    }

    // Keepalive ping for Chrome
    window._ttsKeepAliveTimer = setInterval(() => {
      try {
        if (window.speechSynthesis && window.speechSynthesis.speaking) {
          window.speechSynthesis.pause();
          window.speechSynthesis.resume();
        }
      } catch (_) {}
    }, 8000);

    // Give 60ms for any pending cancellation to settle in the browser
    setTimeout(speakNext, 60);
  }

  function copyTextToClipboard(text, triggerBtn) {
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      if (triggerBtn) {
        const originalText = triggerBtn.getAttribute('data-original-label') || triggerBtn.innerHTML;
        triggerBtn.setAttribute('data-original-label', originalText);
        triggerBtn.classList.add('copied');
        triggerBtn.innerHTML = `
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
          <span>Copied!</span>
        `;
        setTimeout(() => {
          triggerBtn.innerHTML = originalText;
          triggerBtn.classList.remove('copied');
        }, 2000);
      }
    }).catch((err) => {
      console.error('Failed to copy text:', err);
    });
  }

  function downloadBlob(filename, content, mimeType = 'text/plain') {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function sendFeedback(messageId, rating, btn) {
    if (!messageId) return;
    try {
      const csrfMeta = document.querySelector('meta[name="csrf-token"]');
      const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';

      const response = await fetch('/api/feedback', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
        },
        body: JSON.stringify({
          message_id: parseInt(messageId, 10),
          rating: rating,
        }),
      });

      if (response.ok) {
        const parent = btn.closest('.msg-actions');
        if (parent) {
          parent.querySelectorAll('button[data-action="like"], button[data-action="dislike"]').forEach((b) => {
            b.classList.remove('active');
          });
        }
        btn.classList.add('active');
      }
    } catch (err) {
      console.error('Failed to submit feedback:', err);
    }
  }

  function buildUserActionBar() {
    const bar = document.createElement('div');
    bar.className = 'msg-actions user-actions';

    bar.innerHTML = `
      <button type="button" class="action-btn" data-action="copy" title="Copy prompt">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
        </svg>
        <span>Copy</span>
      </button>
      <button type="button" class="action-btn" data-action="edit" title="Edit and resend">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 20h9"></path>
          <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
        </svg>
        <span>Edit</span>
      </button>
      <button type="button" class="action-btn" data-action="undo" title="Undo message">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="1 4 1 10 7 10"></polyline>
          <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
        </svg>
        <span>Undo</span>
      </button>
    `;
    return bar;
  }

  function buildAssistantActionBar() {
    const bar = document.createElement('div');
    bar.className = 'msg-actions assistant-actions';

    bar.innerHTML = `
      <button type="button" class="action-btn" data-action="copy" title="Copy response">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
        </svg>
        <span>Copy</span>
      </button>
      <button type="button" class="action-btn" data-action="regenerate" title="Regenerate answer">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"></polyline>
          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
        </svg>
        <span>Regenerate</span>
      </button>
      <button type="button" class="action-btn icon-only" data-action="like" title="Good response">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path>
        </svg>
      </button>
      <button type="button" class="action-btn icon-only" data-action="dislike" title="Bad response">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"></path>
        </svg>
      </button>
      <button type="button" class="action-btn" data-action="speak" title="Read aloud">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
          <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>
        </svg>
        <span>Read aloud</span>
      </button>
      <button type="button" class="action-btn" data-action="export-pdf" title="Export response as PDF">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="16" y1="13" x2="8" y2="13"></line>
          <line x1="16" y1="17" x2="8" y2="17"></line>
        </svg>
        <span>Export PDF</span>
      </button>
      <button type="button" class="action-btn" data-action="download-md" title="Download response as Markdown">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        <span>Download .md</span>
      </button>
    `;
    return bar;
  }

  function exportMessageToPdf(messageEl) {
    if (!messageEl) return;
    const contentEl = messageEl.querySelector('.message-content');
    if (!contentEl) return;

    const printWindow = window.open('', '_blank', 'width=880,height=920');
    if (!printWindow) {
      alert('Please allow popups to export PDF documents.');
      return;
    }

    const titleEl = document.querySelector('.chat-title') || document.querySelector('.current-session-title');
    const titleText = (titleEl ? titleEl.textContent : '').trim() || 'CloudGPT Architecture Document';
    const htmlContent = contentEl.innerHTML;

    printWindow.document.write(`<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>${titleText} - CloudGPT Export</title>
  <style>
    @page { margin: 20mm 15mm; size: auto; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      color: #111827;
      background: #ffffff;
      line-height: 1.6;
      margin: 0;
      padding: 24px;
    }
    .export-header {
      border-bottom: 2px solid #6366f1;
      padding-bottom: 12px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .export-brand {
      font-size: 20px;
      font-weight: 700;
      color: #4f46e5;
      letter-spacing: -0.02em;
    }
    .export-meta {
      font-size: 12px;
      color: #6b7280;
    }
    h1, h2, h3, h4 { color: #111827; margin-top: 1.4em; margin-bottom: 0.5em; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13px; }
    th, td { border: 1px solid #d1d5db; padding: 8px 12px; text-align: left; }
    th { background: #f3f4f6; font-weight: 600; }
    pre, code { font-family: "SFMono-Regular", Consolas, Menlo, monospace; background: #f3f4f6; }
    pre { padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; border: 1px solid #e5e7eb; }
    code { padding: 2px 4px; border-radius: 4px; font-size: 12px; }
    blockquote { border-left: 4px solid #6366f1; padding-left: 14px; margin-left: 0; color: #4b5563; }
    .msg-actions, .citations-panel, .thinking-badge, .usage-summary, .code-copy-btn, .code-save-file-btn, .table-export-csv-btn, .svg-preview-header {
      display: none !important;
    }
    .svg-preview-card { border: none !important; box-shadow: none !important; padding: 0 !important; }
    .svg-preview-body svg { max-width: 100%; height: auto; }
  </style>
</head>
<body>
  <div class="export-header">
    <div class="export-brand">CloudGPT &mdash; Architecture & Infrastructure Report</div>
    <div class="export-meta">Generated: ${new Date().toLocaleString()}</div>
  </div>
  <div class="export-content">
    ${htmlContent}
  </div>
  <script>
    window.onload = function() {
      setTimeout(function() {
        window.print();
      }, 400);
    };
  <\/script>
</body>
</html>`);
    printWindow.document.close();
  }

  function startInlineEdit(messageEl) {
    const textEl = messageEl.querySelector('.msg-text') || messageEl.querySelector('.message-content');
    if (!textEl || messageEl.classList.contains('is-editing')) return;

    const rawText = messageEl.dataset.rawText || textEl.textContent.trim();
    messageEl.classList.add('is-editing');

    const editContainer = document.createElement('div');
    editContainer.className = 'inline-edit-container';

    const textarea = document.createElement('textarea');
    textarea.className = 'inline-edit-textarea';
    textarea.value = rawText;
    textarea.rows = Math.min(Math.max(rawText.split('\n').length, 2), 10);

    const btnRow = document.createElement('div');
    btnRow.className = 'inline-edit-btn-row';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'edit-btn-cancel';
    cancelBtn.textContent = 'Cancel';

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'edit-btn-save';
    saveBtn.textContent = 'Save & Resend';

    btnRow.append(cancelBtn, saveBtn);
    editContainer.append(textarea, btnRow);

    const originalDisplay = textEl.style.display;
    textEl.style.display = 'none';
    textEl.parentNode.insertBefore(editContainer, textEl.nextSibling);

    textarea.focus();

    cancelBtn.addEventListener('click', () => {
      editContainer.remove();
      textEl.style.display = originalDisplay;
      messageEl.classList.remove('is-editing');
    });

    saveBtn.addEventListener('click', () => {
      const newText = textarea.value.trim();
      if (!newText) return;
      editContainer.remove();
      textEl.style.display = originalDisplay;
      messageEl.classList.remove('is-editing');

      const messageId = messageEl.dataset.messageId;
      if (window.CloudGPTChat && typeof window.CloudGPTChat.resendFromMessage === 'function') {
        window.CloudGPTChat.resendFromMessage(messageEl, messageId, newText);
      }
    });
  }

  function enhanceCodeBlocksAndTables(containerEl) {
    if (!containerEl) return;

    // Enhance code blocks with "Save file" button
    const codeHeaders = containerEl.querySelectorAll('.code-block-header');
    codeHeaders.forEach((header) => {
      if (header.querySelector('.code-save-file-btn')) return;

      const codeEl = header.nextElementSibling?.querySelector('code') || header.parentNode.querySelector('pre code');
      const langLabel = header.querySelector('.code-lang-label')?.textContent?.trim() || 'txt';

      const saveBtn = document.createElement('button');
      saveBtn.type = 'button';
      saveBtn.className = 'code-save-file-btn';
      saveBtn.title = 'Save as file';
      saveBtn.innerHTML = `
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        <span>Save file</span>
      `;

      saveBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const codeText = codeEl ? codeEl.textContent : '';
        const ext = getExtensionForLanguage(langLabel);
        downloadBlob(`snippet.${ext}`, codeText);
      });

      header.insertBefore(saveBtn, header.querySelector('.code-copy-btn'));
    });

    // Enhance markdown tables with "Export CSV" button
    const tables = containerEl.querySelectorAll('table');
    tables.forEach((table) => {
      if (table.dataset.hasCsvExport === 'true') return;
      table.dataset.hasCsvExport = 'true';

      const wrapper = document.createElement('div');
      wrapper.className = 'table-export-wrapper';
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);

      const exportBtn = document.createElement('button');
      exportBtn.type = 'button';
      exportBtn.className = 'table-export-csv-btn';
      exportBtn.innerHTML = `
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        <span>Export CSV</span>
      `;

      exportBtn.addEventListener('click', () => {
        const rows = Array.from(table.querySelectorAll('tr'));
        const csvRows = rows.map((tr) => {
          const cells = Array.from(tr.querySelectorAll('th, td'));
          return cells.map((cell) => {
            let val = cell.textContent.trim().replace(/"/g, '""');
            return `"${val}"`;
          }).join(',');
        });
        downloadBlob('table-export.csv', csvRows.join('\n'), 'text/csv');
      });

      wrapper.insertBefore(exportBtn, table);
    });

    // Render SVG visual diagrams with preview and download
    const codeBlocks = containerEl.querySelectorAll('pre code');
    codeBlocks.forEach((codeEl) => {
      const text = codeEl.textContent.trim();
      if ((text.startsWith('<svg') || text.includes('<svg xmlns')) && text.includes('</svg>') && !codeEl.dataset.hasSvgPreview) {
        codeEl.dataset.hasSvgPreview = 'true';
        const preEl = codeEl.closest('pre');
        const parent = preEl ? preEl.parentNode : null;
        if (!parent || parent.querySelector('.svg-preview-card')) return;

        const svgStart = text.indexOf('<svg');
        const svgEnd = text.lastIndexOf('</svg>') + 6;
        const svgMarkup = text.substring(svgStart, svgEnd);

        const card = document.createElement('div');
        card.className = 'svg-preview-card';
        card.innerHTML = `
          <div class="svg-preview-header">
            <div class="svg-preview-title">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                <circle cx="8.5" cy="8.5" r="1.5"></circle>
                <polyline points="21 15 16 10 5 21"></polyline>
              </svg>
              <span>Architecture Diagram</span>
            </div>
            <button type="button" class="svg-download-btn">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
              <span>Download SVG</span>
            </button>
          </div>
          <div class="svg-preview-body">${svgMarkup}</div>
        `;

        card.querySelector('.svg-download-btn').addEventListener('click', () => {
          downloadBlob('architecture-diagram.svg', svgMarkup, 'image/svg+xml');
        });

        parent.insertBefore(card, preEl);
      }
    });
  }

  function getExtensionForLanguage(lang) {
    const l = (lang || '').toLowerCase();
    const map = {
      python: 'py', py: 'py', javascript: 'js', js: 'js', typescript: 'ts', ts: 'ts',
      json: 'json', yaml: 'yaml', yml: 'yaml', bash: 'sh', sh: 'sh', shell: 'sh',
      terraform: 'tf', tf: 'tf', html: 'html', css: 'css', sql: 'sql', dockerfile: 'dockerfile',
    };
    return map[l] || 'txt';
  }

  function renderArtifactCard(artifact, containerEl) {
    if (!artifact || !containerEl) return;

    let artifactList = containerEl.querySelector('.artifact-list');
    if (!artifactList) {
      artifactList = document.createElement('div');
      artifactList.className = 'artifact-list';
      containerEl.append(artifactList);
    }

    const card = document.createElement('div');
    card.className = 'artifact-card';

    const iconSpan = document.createElement('span');
    iconSpan.className = 'artifact-icon';
    iconSpan.innerHTML = `
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
        <polyline points="14 2 14 8 20 8"></polyline>
      </svg>
    `;

    const infoDiv = document.createElement('div');
    infoDiv.className = 'artifact-info';
    const nameSpan = document.createElement('span');
    nameSpan.className = 'artifact-name';
    nameSpan.textContent = artifact.filename;

    const sizeSpan = document.createElement('span');
    sizeSpan.className = 'artifact-size';
    sizeSpan.textContent = window.CloudGPTAttachments ? window.CloudGPTAttachments.formatFileSize(artifact.size) : `${artifact.size} B`;

    infoDiv.append(nameSpan, sizeSpan);

    const downloadBtn = document.createElement('a');
    downloadBtn.className = 'artifact-download-btn';
    downloadBtn.href = `/api/artifacts/${encodeURIComponent(artifact.id || artifact.artifact_id)}/download`;
    downloadBtn.download = artifact.filename;
    downloadBtn.innerHTML = `
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="7 10 12 15 17 10"></polyline>
        <line x1="12" y1="15" x2="12" y2="3"></line>
      </svg>
      <span>Download</span>
    `;

    card.append(iconSpan, infoDiv, downloadBtn);
    artifactList.append(card);
  }

  function initActionsDelegation(chatContainer) {
    if (!chatContainer) return;

    chatContainer.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;

      const action = btn.dataset.action;
      const messageEl = btn.closest('.message');
      if (!messageEl) return;

      const isUser = messageEl.classList.contains('user');
      const messageId = messageEl.dataset.messageId;
      const rawText = messageEl.dataset.rawText || (messageEl.querySelector('.msg-text')?.textContent || messageEl.querySelector('.message-content')?.textContent || '').trim();

      if (action === 'copy') {
        copyTextToClipboard(rawText, btn);
      } else if (action === 'edit' && isUser) {
        startInlineEdit(messageEl);
      } else if (action === 'undo' && isUser) {
        if (window.CloudGPTChat && typeof window.CloudGPTChat.undoLastMessage === 'function') {
          window.CloudGPTChat.undoLastMessage(messageEl, messageId);
        }
      } else if (action === 'regenerate' && !isUser) {
        if (window.CloudGPTChat && typeof window.CloudGPTChat.regenerateFromMessage === 'function') {
          window.CloudGPTChat.regenerateFromMessage(messageEl, messageId);
        }
      } else if (action === 'like' && !isUser) {
        sendFeedback(messageId, 1, btn);
      } else if (action === 'dislike' && !isUser) {
        sendFeedback(messageId, -1, btn);
      } else if (action === 'speak' && !isUser) {
        toggleReadAloud(rawText, btn);
      } else if (action === 'export-pdf' && !isUser) {
        exportMessageToPdf(messageEl);
      } else if (action === 'download-md' && !isUser) {
        downloadBlob('answer.md', rawText, 'text/markdown');
      }
    });
  }

  window.CloudGPTActions = {
    buildUserActionBar,
    buildAssistantActionBar,
    enhanceCodeBlocksAndTables,
    renderArtifactCard,
    initActionsDelegation,
    downloadBlob,
  };
})();
