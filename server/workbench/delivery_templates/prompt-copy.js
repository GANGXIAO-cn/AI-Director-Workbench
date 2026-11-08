document.querySelectorAll('section pre, [data-publish-pack] pre[data-copy-label]').forEach((block, index) => {
  block.id = 'prompt-' + (index + 1);
  const bar = document.createElement('div');
  const button = document.createElement('button');
  const status = document.createElement('span');
  button.type = 'button';
  button.textContent = block.dataset.copyLabel || '复制提示词';
  button.setAttribute('aria-controls', block.id);
  status.setAttribute('role', 'status');
  status.style.marginLeft = '12px';
  bar.style.marginTop = '16px';
  bar.append(button, status);
  block.before(bar);
  button.addEventListener('click', async () => {
    const text = block.textContent;
    let copied = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        copied = true;
      }
    } catch (_) {}
    if (!copied) {
      const input = document.createElement('textarea');
      input.value = text;
      input.style.cssText = 'position:fixed;left:-10000px;top:0';
      document.body.append(input);
      input.select();
      try { copied = document.execCommand('copy'); } catch (_) {}
      input.remove();
      button.focus({preventScroll:true});
    }
    status.textContent = copied ? '已复制' : '复制未成功，请选中下方内容手动复制';
  });
});
