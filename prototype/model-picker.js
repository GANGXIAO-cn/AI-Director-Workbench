/* Sample choices for the offline review prototype. Provider availability is checked by the local service. */
const demoModels = {
  Codex: {
    'gpt-6.1-sol': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    'gpt-6-astra': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']
  },
  'Claude Code': {
    sonnet: ['low', 'medium', 'high'],
    opus: ['low', 'medium', 'high']
  },
  'Google Antigravity': {
    'Gemini 3.8 Flash': ['low', 'medium', 'high'],
    'claude-sonnet-4-6': [],
    'claude-opus-4-6-thinking': []
  }
};
const pickerScope = {
  '58': ['shot-design', '镜头 K05 方案比较'],
  '47': ['reference', '本集拉片任务'],
  '49': ['adaptation', '本集改编任务'],
  '50': ['adaptation-review', '故事独审任务'],
  '51': ['adaptation-revision', '本集修订任务'],
  '52': ['adapted-script', '分场剧本任务'],
  '35': ['project', '项目默认'],
  '09': ['story', '当前故事任务'],
  '10': ['review', '当前审稿任务'],
  '27': ['revision', '当前修订任务'],
  '36': ['revision', '当前修订任务']
};

function mountModelPicker(screen) {
  if (screen === '34') {
    const snapshot = read('selection:last-submission', '');
    if (snapshot) {
      const {provider, model, effort} = JSON.parse(snapshot);
      const summary = document.createElement('p');
      summary.className = 'panel';
      summary.textContent = '提交时选择（演示） · ' + [provider, model || '执行器默认', effort || '模型默认'].join(' / ');
      document.querySelector('main .two section').prepend(summary);
    }
    return;
  }
  const scope = pickerScope[screen];
  if (!scope) return;
  const project = read('selection:project', '{}');
  const selection = JSON.parse(read('selection:' + scope[0], project));
  const panel = document.createElement('fieldset');
  panel.className = 'panel model-picker';
  const legend = document.createElement('legend');
  legend.textContent = scope[1] + ' · 模型配置（演示）';
  panel.append(legend);

  function select(label, initial, options) {
    const wrapper = document.createElement('label');
    wrapper.textContent = label;
    const control = document.createElement('select');
    for (const [value, text] of options) control.add(new Option(text, value));
    if (options.some(([value]) => value === initial)) control.value = initial;
    wrapper.append(control);
    panel.append(wrapper);
    return control;
  }

  const options = values => values.map(value => [value, value]);
  const provider = select('执行器', selection.provider, options(Object.keys(demoModels)));
  const models = demoModels[provider.value];
  const model = select('模型型号', selection.model, [['', '执行器默认'], ...options(Object.keys(models))]);
  const levels = models[model.value] || [];
  const variant = provider.value === 'Google Antigravity' && model.value === 'Gemini 3.8 Flash';
  const effort = select('推理强度', selection.effort,
    [['', variant ? '请选择强度' : '模型默认'], ...options(levels)]);
  const save = () => write('selection:' + scope[0], JSON.stringify({
    provider: provider.value, model: model.value, effort: effort.value
  }));
  provider.onchange = () => {
    write('selection:' + scope[0], JSON.stringify({provider: provider.value}));
    refresh();
  };
  model.onchange = () => {
    const supported = models[model.value] || [];
    write('selection:' + scope[0], JSON.stringify({
      provider: provider.value, model: model.value,
      effort: supported.includes(effort.value) ? effort.value : ''
    }));
    refresh();
  };
  function refresh() {
    mountModelPicker(screen);
    panel.remove();
  }
  effort.onchange = save;
  if (screen === '36') {
    const submit = document.querySelector('button.primary[data-go="34"]');
    submit.disabled = variant && !effort.value;
    effort.onchange = () => {
      save();
      submit.disabled = variant && !effort.value;
    };
    submit.dataset.modelSubmit = 'true';
  }
  document.querySelector('main .two section').prepend(panel);
}
