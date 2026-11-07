// Original camera-framing diagrams. Coordinates share a 320 × 180 frame across views.
function shotDiagram(view) {
  const shapes = [];
  const rectangle = (x, y, w, h, color) => shapes.push(['rect', x, y, w, h, color]);
  const oval = (x, y, w, h, color) => shapes.push(['ellipse', x, y, w, h, color]);
  const polygon = (points, color) => shapes.push(['polygon', points, color]);
  const night = '#202D39', hull = '#455966', wood = '#906C54', paper = '#EAD9A8';
  const wet = '#ADCBCD', skin = '#CA9B7C', sleeve = '#426F79', coat = '#465366';
  rectangle(0, 0, 320, 180, night);
  function windowFrame() {
    rectangle(0, 0, 320, 107, hull);
    rectangle(12, 12, 137, 79, '#293D4A');
    rectangle(161, 12, 147, 79, '#293D4A');
    rectangle(0, 85, 320, 8, '#748588');
    for (const x of [17, 49, 62, 180, 221, 267]) rectangle(x, 69, 5, 2, '#CAB47E');
  }
  function tickets(x, y, scale, gap) {
    const part = (pts, color, dx=0) => polygon(pts.map(([a,b])=>[x+a*scale+dx,y+b*scale]),color);
    part([[0,0],[40,0],[35,9],[41,16],[36,22],[40,30],[0,30]],paper);
    part([[42,0],[82,0],[82,30],[42,30],[38,22],[43,16],[37,9]],wet,gap);
    rectangle(x+6*scale,y+7*scale,21*scale,2*scale,'#906C54');
    rectangle(x+52*scale+gap,y+7*scale,19*scale,2*scale,'#647C7C');
  }
  if (view === 'stamp' || view === 'stamp-open') {
    rectangle(0,0,320,112,'#58605B');rectangle(0,112,320,68,'#7B6958');
    rectangle(24,18,271,98,'#434E4E');
    rectangle(40,57,109,84,'#DBC69B');const x=view==='stamp'?151:194;
    rectangle(x,57,104,84,'#E1CBA0');
    polygon([[125,74],[148,71],[148,117],[119,111],[106,92]],'#975B4D');
    polygon([[x,71],[x+23,74],[x+39,92],[x+26,111],[x,117]],'#975B4D');
  } else if (view === 'top' || view === 'top-open') {
    rectangle(0,0,320,180,'#795F50');oval(40,2,253,173,'#AD8B66');
    rectangle(25,19,64,18,'#3D5158');rectangle(35,15,42,8,'#879496');
    tickets(103,70,1.4,view==='top-open'?30:0);
    polygon([[0,127],[66,98],[91,105],[82,121],[0,161]],sleeve);
    polygon([[65,99],[92,85],[120,87],[120,93],[98,97],[88,115],[75,120]],skin);
    polygon([[320,91],[265,81],[246,94],[264,107],[320,124]],coat);
    polygon([[262,82],[246,76],[215,80],[207,87],[230,90],[246,100],[262,99]],skin);
  } else if (view === 'reaction' || view === 'native') {
    windowFrame();rectangle(0,131,320,49,wood);
    polygon([[0,180],[15,124],[47,114],[74,120],[106,180]],sleeve);
    oval(34,62,42,60,skin);polygon([[29,75],[41,55],[65,55],[78,68],[70,78],[34,85]],'#31353B');
    rectangle(68,84,5,3,night);
    rectangle(197,39,9,93,'#B19B7B');rectangle(152,108,106,8,'#A49B89');
    polygon([[64,130],[98,120],[147,115],[156,126],[99,138],[73,145]],sleeve);
    polygon([[144,116],[164,106],[188,106],[190,110],[173,115],[158,127]],skin);
    if(view==='native'){rectangle(163,140,137,8,'#7E9090');polygon([[171,143],[297,145],[310,163],[182,162]],'#B3A385');}
  } else {
    windowFrame();polygon([[0,123],[320,100],[320,180],[0,180]],wood);
    polygon([[0,180],[0,151],[64,122],[99,131],[83,160]],sleeve);
    oval(33,58,29,40,'#947C6F');polygon([[27,68],[41,50],[58,53],[65,63],[57,71]],night);
    polygon([[184,97],[236,94],[277,137],[120,146]],'#BAA47D');
    tickets(125,110,.95,view==='side-open'?26:0);
    polygon([[81,136],[107,118],[132,117],[139,122],[120,127],[99,142]],skin);
    polygon([[320,93],[285,95],[253,108],[261,118],[320,107]],coat);
    polygon([[254,106],[236,109],[212,110],[207,116],[231,119],[260,115]],skin);
  }
  return shapes;
}

function mountShotDiagrams() {
  for (const canvas of document.querySelectorAll('canvas[data-shot-view]')) {
    const context = canvas.getContext('2d');
    if (!context) continue;
    context.scale(canvas.width/320, canvas.height/180);
    for (const [kind,...args] of shotDiagram(canvas.dataset.shotView)) {
      context.fillStyle = args[args.length-1];
      if (kind === 'rect') context.fillRect(...args.slice(0,4));
      else if (kind === 'ellipse') {
        const [x,y,w,h]=args;
        context.beginPath();context.ellipse(x+w/2,y+h/2,w/2,h/2,0,0,Math.PI*2);context.fill();
      } else {
        context.beginPath();args[0].forEach(([x,y],i)=>i ? context.lineTo(x,y) : context.moveTo(x,y));
        context.closePath();context.fill();
      }
    }
  }
}
