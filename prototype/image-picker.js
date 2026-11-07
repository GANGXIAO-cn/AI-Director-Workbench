// Offline examples from the image MCP configuration. The workbench must discover live capabilities.
const imageRoutes = ['Codex', 'Google Antigravity', 'MCP 图像工具', '图像 API 直连'];
const imageDefaults = {route: '图像 API 直连', operation: '编辑参考图', model: '服务默认', quality: '服务默认'};
const mcpRoute = 'MCP 图像工具';

function currentImageBatch() {
  const project = read('images:project', JSON.stringify(imageDefaults));
  return JSON.parse(read('images:batch', project));
}

function imageTool(config) {
  if (config.route !== mcpRoute) return config.route;
  return 'openai_images / ' + (config.operation === '编辑参考图' ? 'edit_image' : 'generate_image');
}

function imageTaskScreen() {
  const stored = read('images:submitted', '');
  if (!stored) return null;
  const batch = JSON.parse(stored);
  return {title: '图像批次详情', subtitle: '末班渡轮 · 素材准备', type: 'task',
    action: ['返回素材准备', '20'], sideTitle: '提交时的选择', side: [
      batch.route, imageTool(batch), batch.model + ' / ' + batch.quality
    ].filter((value, i, array) => array.indexOf(value) === i),
    banner: '批次确认演示已完成。费用未知，尚无实际图片或服务回执。',
    headers: ['操作', '范围', '状态', '查看'],
    rows: [[batch.operation, batch.scope, '已确认（演示）', '41']],
    rules: ['此次记录保留确认时的通道、工具及参数。']};
}

function mountImagePicker(screen) {
  if (!['20', '35', '41'].includes(screen)) return;
  const project = screen === '35';
  const key = project ? 'images:project' : 'images:batch';
  const config = project ? JSON.parse(read(key, JSON.stringify(imageDefaults))) : currentImageBatch();
  const panel = document.createElement('fieldset');
  panel.className = 'panel model-picker image-picker';

  function paint() {
    panel.replaceChildren();
    const legend = document.createElement('legend');
    legend.textContent = project ? '项目默认出图通道' : '本批出图配置';
    panel.append(legend);
    function field(label, name, options) {
      const wrapper = document.createElement('label');
      wrapper.textContent = label;
      const select = document.createElement('select');
      select.setAttribute('aria-label', label);
      for (const value of options) select.add(new Option(value, value));
      select.value = config[name];
      select.onchange = () => {
        config[name] = select.value;
        if (name === 'route') Object.assign(config, {model: '服务默认', quality: '服务默认'});
        write(key, JSON.stringify(config));
        paint();
      };
      wrapper.append(select);
      panel.append(wrapper);
    }
    field('出图通道', 'route', imageRoutes);
    if (!project) field('操作', 'operation', ['生成新图', '编辑参考图']);
    const mcp = config.route === mcpRoute;
    field('图像型号', 'model', mcp ? ['服务默认', 'gpt-image-2.5-sunburst', 'gpt-image-2.5-flare', 'gpt-image-2', 'gpt-image-1.5'] : ['服务默认']);
    field('输出质量', 'quality', mcp ? ['服务默认', 'low', 'medium', 'high'] : ['服务默认']);
    const note = document.createElement('small');
    note.textContent = mcp ? (project ? 'MCP 服务 · openai_images' : 'MCP 服务与工具 · ' + imageTool(config))
      : config.route + ' · 图像型号和参数待检测';
    panel.append(note);
  }
  if (!project && !read(key, '')) write(key, JSON.stringify(config));
  paint();
  document.querySelector('main .two section').prepend(panel);
  if (screen === '41') {
    const submit = document.querySelector('button.primary[data-go="image-task"]');
    submit.addEventListener('click', () => {
      const scope = document.querySelector('#fields [name="f0"]').value;
      write('images:submitted', JSON.stringify({...config, scope}));
    });
  }
}
