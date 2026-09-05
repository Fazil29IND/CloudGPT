/**
 * CloudGPT — Multimodal Attachments & Drag-Drop/Paste Handler
 * Handles file uploading, drag-and-drop, clipboard image paste,
 * attachment chip rendering, and thumbnail previews.
 */

(function () {
  'use strict';

  function formatFileSize(bytes) {
    if (!bytes || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const size = (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1);
    return `${size} ${units[i]}`;
  }

  function getFileKind(filename, contentType) {
    const ext = (filename || '').split('.').pop().toLowerCase();
    const imageExts = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'svg'];
    const audioExts = ['mp3', 'wav', 'm4a', 'ogg', 'webm', 'flac'];
    const codeExts = ['py', 'js', 'ts', 'jsx', 'tsx', 'json', 'yaml', 'yml', 'tf', 'sh', 'sql', 'html', 'css', 'xml', 'dockerfile'];

    if (imageExts.includes(ext) || (contentType && contentType.startsWith('image/'))) return 'image';
    if (audioExts.includes(ext) || (contentType && contentType.startsWith('audio/'))) return 'audio';
    if (codeExts.includes(ext)) return 'code';
    if (['pdf', 'docx', 'xlsx', 'csv', 'txt', 'md'].includes(ext)) return 'doc';
    return 'file';
  }

  function getFileIconSvg(kind) {
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '14');
    svg.setAttribute('height', '14');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');

    if (kind === 'image') {
      const rect = document.createElementNS(svgNS, 'rect');
      rect.setAttribute('x', '3'); rect.setAttribute('y', '3');
      rect.setAttribute('width', '18'); rect.setAttribute('height', '18');
      rect.setAttribute('rx', '2'); rect.setAttribute('ry', '2');
      const circle = document.createElementNS(svgNS, 'circle');
      circle.setAttribute('cx', '8.5'); circle.setAttribute('cy', '8.5'); circle.setAttribute('r', '1.5');
      const poly = document.createElementNS(svgNS, 'polyline');
      poly.setAttribute('points', '21 15 16 10 5 21');
      svg.append(rect, circle, poly);
    } else if (kind === 'audio') {
      const path = document.createElementNS(svgNS, 'path');
      path.setAttribute('d', 'M9 18V5l12-2v13');
      const c1 = document.createElementNS(svgNS, 'circle');
      c1.setAttribute('cx', '6'); c1.setAttribute('cy', '18'); c1.setAttribute('r', '3');
      const c2 = document.createElementNS(svgNS, 'circle');
      c2.setAttribute('cx', '18'); c2.setAttribute('cy', '16'); c2.setAttribute('r', '3');
      svg.append(path, c1, c2);
    } else if (kind === 'code') {
      const p1 = document.createElementNS(svgNS, 'polyline');
      p1.setAttribute('points', '16 18 22 12 16 6');
      const p2 = document.createElementNS(svgNS, 'polyline');
      p2.setAttribute('points', '8 6 2 12 8 18');
      svg.append(p1, p2);
    } else {
      const path = document.createElementNS(svgNS, 'path');
      path.setAttribute('d', 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z');
      const poly = document.createElementNS(svgNS, 'polyline');
      poly.setAttribute('points', '14 2 14 8 20 8');
      svg.append(path, poly);
    }
    return svg;
  }

  async function uploadSingleFile(file, csrfToken) {
    const formData = new FormData();
    formData.append('file', file, file.name);

    const response = await fetch('/api/upload', {
      method: 'POST',
      headers: {
        'X-CSRF-Token': csrfToken,
      },
      body: formData,
    });

    if (!response.ok) {
      let errMsg = 'Failed to upload attachment';
      try {
        const errJson = await response.json();
        errMsg = errJson.detail || errMsg;
      } catch (_) {}
      throw new Error(errMsg);
    }

    return await response.json();
  }

  function renderAttachmentChips(attachments, targetContainer) {
    if (!attachments || !attachments.length || !targetContainer) return;

    let attachRow = targetContainer.querySelector('.msg-attachments');
    if (!attachRow) {
      attachRow = document.createElement('div');
      attachRow.className = 'msg-attachments';
      targetContainer.prepend(attachRow);
    } else {
      attachRow.replaceChildren();
    }

    attachments.forEach((att) => {
      const kind = att.kind || getFileKind(att.filename || att.name, att.content_type || att.type);
      const filename = att.filename || att.name || 'attachment';
      const size = att.size ? formatFileSize(att.size) : '';
      const attachmentId = att.attachment_id || '';

      const chip = document.createElement('a');
      chip.className = 'attach-chip';
      chip.dataset.kind = kind;
      if (attachmentId) {
        chip.href = `/api/attachments/${encodeURIComponent(attachmentId)}/download`;
        chip.target = '_blank';
        chip.title = `Download ${filename}`;
      } else {
        chip.href = '#';
        chip.title = filename;
      }

      const iconSpan = document.createElement('span');
      iconSpan.className = 'attach-icon';
      iconSpan.append(getFileIconSvg(kind));

      const nameSpan = document.createElement('span');
      nameSpan.className = 'attach-name';
      nameSpan.textContent = filename;

      chip.append(iconSpan, nameSpan);

      if (size) {
        const sizeSpan = document.createElement('span');
        sizeSpan.className = 'attach-size';
        sizeSpan.textContent = size;
        chip.append(sizeSpan);
      }

      attachRow.append(chip);

      // Inline thumbnail for image attachments
      if (kind === 'image') {
        const thumbWrapper = document.createElement('div');
        thumbWrapper.className = 'image-thumb-wrapper';
        const img = document.createElement('img');
        img.className = 'image-thumb';
        img.alt = filename;
        img.loading = 'lazy';

        if (att.previewUrl) {
          img.src = att.previewUrl;
        } else if (attachmentId) {
          img.src = `/api/attachments/${encodeURIComponent(attachmentId)}/download`;
        } else if (att.image_base64) {
          img.src = `data:${att.content_type || 'image/png'};base64,${att.image_base64}`;
        }

        img.addEventListener('click', (e) => {
          e.preventDefault();
          window.open(img.src, '_blank');
        });

        thumbWrapper.append(img);
        attachRow.append(thumbWrapper);
      }
    });
  }

  function setupDragAndDrop(dropTargetEl, onFilesAdded) {
    if (!dropTargetEl) return;

    ['dragenter', 'dragover'].forEach((eventName) => {
      dropTargetEl.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropTargetEl.classList.add('drag-active');
      }, false);
    });

    ['dragleave', 'drop'].forEach((eventName) => {
      dropTargetEl.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropTargetEl.classList.remove('drag-active');
      }, false);
    });

    dropTargetEl.addEventListener('drop', (e) => {
      const dt = e.dataTransfer;
      const files = Array.from(dt.files || []);
      if (files.length > 0 && typeof onFilesAdded === 'function') {
        onFilesAdded(files);
      }
    }, false);
  }

  function setupClipboardPaste(textareaEl, onFilesAdded) {
    const target = textareaEl || document;
    target.addEventListener('paste', (e) => {
      const clipboard = e.clipboardData;
      if (!clipboard || !clipboard.items) return;

      const pastedFiles = [];
      for (let i = 0; i < clipboard.items.length; i++) {
        const item = clipboard.items[i];
        if (item.kind === 'file') {
          const file = item.getAsFile();
          if (file) {
            // Generate a friendlier filename for pasted screenshots
            let fileName = file.name;
            if (!fileName || fileName === 'image.png') {
              const now = new Date();
              fileName = `Screenshot_${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}.png`;
            }
            const namedFile = new File([file], fileName, { type: file.type });
            pastedFiles.push(namedFile);
          }
        }
      }

      if (pastedFiles.length > 0) {
        e.preventDefault();
        if (typeof onFilesAdded === 'function') {
          onFilesAdded(pastedFiles);
        }
      }
    });
  }

  window.CloudGPTAttachments = {
    formatFileSize,
    getFileKind,
    getFileIconSvg,
    uploadSingleFile,
    renderAttachmentChips,
    setupDragAndDrop,
    setupClipboardPaste,
  };
})();
