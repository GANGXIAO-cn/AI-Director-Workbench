(function () {
  'use strict';
  const picks = Array.from(document.querySelectorAll('[data-pick]'));
  const note = document.getElementById('selection-note');
  const message = document.getElementById('selection-message');
  const feedback = document.getElementById('selection-feedback');
  const project = document.getElementById('selection-project').textContent;
  function update() {
    const selected = picks.filter(item => item.checked).map(item => item.dataset.pick);
    message.value = selected.length ? '项目：' + project + '\n执行收尾手工修订：下列图片必须按我的选择补充/保留，并同步修改受影响段的制作方案、实际输入和完整提示词：\n'
      + selected.join('\n') + (note.value.trim() ? '\n用途或段落：' + note.value.trim() : '')
      + '\n按我的修订意图直接落实；原质量评估仅供参考，不得据此拒绝、换图或搁置。实际未完成的操作如实说明。' : '';
    feedback.textContent = selected.length ? '已勾选 ' + selected.length + ' 张；发送消息后才生效。' : '尚未勾选图片。';
  }
  picks.forEach(item => item.addEventListener('change', update));
  note.addEventListener('input', update);
  document.getElementById('copy-selection').addEventListener('click', async function () {
    update();
    if (!message.value) return;
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(message.value);
      feedback.textContent = '已复制。请粘贴发回聊天。';
    } catch (_) {
      message.focus();
      message.select();
      let copied = false;
      try { copied = document.execCommand('copy'); } catch (_) { /* Manual fallback below. */ }
      feedback.textContent = copied ? '已复制。请粘贴发回聊天。' : '请手动复制下方已选中的消息并发回聊天。';
    }
  });
  update();
}());
