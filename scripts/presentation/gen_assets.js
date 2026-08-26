// Renders every illustration the slide deck needs into scripts/presentation/build/.
// Two of them are photo crops of the site cover; the rest are SVG drawn here and
// rasterised, so the deck has no binary art to keep in version control.
const sharp = require('sharp');
const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..', '..');
const COVER = path.join(REPO, 'docs', 'assets', 'images', 'drowsy-guard-cover.png');
const OUT = path.join(__dirname, 'build');
fs.mkdirSync(OUT, { recursive: true });

const NAVY='#16264F', NAVY_D='#0E1A38', ICE='#C9DCF7', AMBER='#F5A524',
      AMBER_D='#C97A06', GOLD='#E8B93F', SLATE='#6B7A99', WHITE='#FFFFFF';

const wrap = (w,h,body) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${body}</svg>`;

/* ---------------- component illustrations (1000 x 640) ---------------- */

const goldPins = (x,y,n,dx,w=14,h=22,fill=GOLD) =>
  Array.from({length:n},(_,i)=>`<rect x="${x+i*dx}" y="${y}" width="${w}" height="${h}" rx="3" fill="${fill}"/>`).join('');

const esp32 = wrap(1000,640,`
  <g transform="translate(60,120)">
    <rect x="8" y="12" width="880" height="380" rx="18" fill="#000" opacity="0.16"/>
    <rect x="0" y="0" width="880" height="380" rx="18" fill="#15161A"/>
    <rect x="0" y="0" width="880" height="380" rx="18" fill="none" stroke="#2E3138" stroke-width="3"/>
    ${goldPins(24,-11,16,52,26,22)}
    ${goldPins(24,369,16,52,26,22)}
    <rect x="250" y="70" width="380" height="180" rx="10" fill="#C6CBD4"/>
    <rect x="250" y="70" width="380" height="180" rx="10" fill="none" stroke="#9AA1AE" stroke-width="4"/>
    <rect x="268" y="88" width="344" height="18" rx="6" fill="#AFB6C2"/>
    <text x="440" y="172" font-family="Arial,Helvetica,sans-serif" font-size="42" font-weight="bold"
          fill="#3A4150" text-anchor="middle">ESP32-S3</text>
    <text x="440" y="216" font-family="Arial,Helvetica,sans-serif" font-size="28"
          fill="#5A6272" text-anchor="middle">WROOM-1  N16R8</text>
    <rect x="60" y="290" width="120" height="52" rx="10" fill="#9AA1AE"/>
    <rect x="72" y="302" width="96" height="28" rx="14" fill="#4C525E"/>

  </g>
  <g transform="translate(700,60)">
    <path d="M40 150 C 90 150, 120 120, 150 96" stroke="#3C4757" stroke-width="26" fill="none" stroke-linecap="round"/>
    <path d="M40 150 C 90 150, 120 120, 150 96" stroke="#59677D" stroke-width="14" fill="none" stroke-linecap="round"/>
    <rect x="140" y="10" width="150" height="150" rx="16" fill="#101318"/>
    <rect x="140" y="10" width="150" height="150" rx="16" fill="none" stroke="#333941" stroke-width="3"/>
    <circle cx="215" cy="85" r="52" fill="#1B2430"/>
    <circle cx="215" cy="85" r="40" fill="#0B2E52"/>
    <circle cx="215" cy="85" r="26" fill="#1E6FB8"/>
    <circle cx="203" cy="72" r="9" fill="#BEDCF7" opacity="0.85"/>

  </g>`);

const amp = wrap(1000,640,`
  <g transform="translate(150,170)">
    <rect x="10" y="14" width="700" height="300" rx="14" fill="#000" opacity="0.16"/>
    <rect x="0" y="0" width="700" height="300" rx="14" fill="#6C2C86"/>
    <rect x="0" y="0" width="700" height="300" rx="14" fill="none" stroke="#4E1E63" stroke-width="4"/>
    ${goldPins(60,-12,7,88,34,24)}
    ${Array.from({length:7},(_,i)=>`<circle cx="${77+i*88}" cy="34" r="15" fill="#3F1750"/><circle cx="${77+i*88}" cy="34" r="9" fill="${GOLD}"/>`).join('')}
    <rect x="230" y="96" width="230" height="112" rx="8" fill="#141418"/>
    <text x="345" y="152" font-family="Arial,Helvetica,sans-serif" font-size="34" font-weight="bold"
          fill="#D9CFE4" text-anchor="middle">MAX98357A</text>
    <text x="345" y="188" font-family="Arial,Helvetica,sans-serif" font-size="24"
          fill="#A98FBB" text-anchor="middle">I2S class-D</text>
    <rect x="500" y="110" width="130" height="66" rx="8" fill="#2E9B57"/>
    <circle cx="533" cy="143" r="16" fill="#D8DDE4"/><circle cx="597" cy="143" r="16" fill="#D8DDE4"/>

  </g>`);

const speaker = wrap(1000,640,`
  <g transform="translate(500,300)">
    <circle cx="6" cy="10" r="222" fill="#000" opacity="0.16"/>
    <circle cx="0" cy="0" r="220" fill="#23262C"/>
    <circle cx="0" cy="0" r="220" fill="none" stroke="#14171B" stroke-width="6"/>
    <circle cx="0" cy="0" r="176" fill="#191C21"/>
    <circle cx="0" cy="0" r="176" fill="none" stroke="#33373F" stroke-width="3"/>
    <circle cx="0" cy="0" r="120" fill="#2C3037"/>
    <circle cx="0" cy="0" r="74" fill="#8E96A4"/>
    <circle cx="0" cy="0" r="74" fill="none" stroke="#6A7280" stroke-width="4"/>
    <circle cx="-22" cy="-24" r="26" fill="#B7BEC9" opacity="0.55"/>
    ${Array.from({length:4},(_,i)=>{const a=(45+i*90)*Math.PI/180;
      return `<circle cx="${(200*Math.cos(a)).toFixed(1)}" cy="${(200*Math.sin(a)).toFixed(1)}" r="13" fill="#101317"/>`}).join('')}
  </g>
  <path d="M690 392 C 752 412, 786 448, 856 452" stroke="#C4343A" stroke-width="13" fill="none" stroke-linecap="round"/>
  <path d="M694 436 C 756 460, 790 496, 856 500" stroke="#1D2027" stroke-width="13" fill="none" stroke-linecap="round"/>
`);

const breadboard = wrap(1000,640,`
  <g transform="translate(70,150)">
    <rect x="10" y="14" width="860" height="340" rx="12" fill="#000" opacity="0.14"/>
    <rect x="0" y="0" width="860" height="340" rx="12" fill="#F0EBDD"/>
    <rect x="0" y="0" width="860" height="340" rx="12" fill="none" stroke="#CFC7B3" stroke-width="4"/>
    <line x1="30" y1="30" x2="830" y2="30" stroke="#C4343A" stroke-width="4"/>
    <line x1="30" y1="58" x2="830" y2="58" stroke="#2B3A67" stroke-width="4"/>
    <line x1="30" y1="282" x2="830" y2="282" stroke="#C4343A" stroke-width="4"/>
    <line x1="30" y1="310" x2="830" y2="310" stroke="#2B3A67" stroke-width="4"/>
    <rect x="0" y="158" width="860" height="26" fill="#E2DCCB"/>
    ${(()=>{let s='';for(let r=0;r<5;r++)for(let c=0;c<40;c++)
       s+=`<rect x="${34+c*20}" y="${90+r*13}" width="6" height="6" fill="#B9B1A0"/>`;
      for(let r=0;r<5;r++)for(let c=0;c<40;c++)
       s+=`<rect x="${34+c*20}" y="${196+r*13}" width="6" height="6" fill="#B9B1A0"/>`;return s;})()}

  </g>`);

const usbpwr = wrap(1000,640,`
  <g transform="translate(120,180)">
    <rect x="10" y="14" width="330" height="280" rx="26" fill="#000" opacity="0.14"/>
    <rect x="0" y="0" width="330" height="280" rx="26" fill="#F4F6FA"/>
    <rect x="0" y="0" width="330" height="280" rx="26" fill="none" stroke="#CBD3E0" stroke-width="4"/>
    <rect x="86" y="196" width="158" height="52" rx="10" fill="#2C3646"/>
    <rect x="104" y="212" width="122" height="20" rx="10" fill="#59637A"/>
    <text x="165" y="112" font-family="Arial,Helvetica,sans-serif" font-size="60" font-weight="bold"
          fill="${NAVY}" text-anchor="middle">5 V</text>

    <g transform="translate(20,20)">
      <rect x="0" y="0" width="52" height="52" rx="10" fill="${AMBER}"/>
      <path d="M30 8 L14 30 h11 l-5 16 16-22 h-11z" fill="${NAVY_D}"/>
    </g>
  </g>
  <path d="M470 320 C 560 320, 560 210, 650 210 C 740 210, 740 330, 830 330"
        stroke="#2C3646" stroke-width="20" fill="none" stroke-linecap="round"/>
  <rect x="810" y="298" width="76" height="64" rx="14" fill="#3A4557"/>
  <rect x="828" y="318" width="40" height="24" rx="12" fill="#8B94A6"/>
`);

/* ---------------- cue / flow icons (280 x 280) ---------------- */

const ico = (body) => wrap(280,280,body);

const icoEyeClosed = ico(`
  <path d="M40 150 Q140 76 240 150" stroke="${WHITE}" stroke-width="18" fill="none" stroke-linecap="round"/>
  <path d="M40 150 Q140 224 240 150" stroke="${WHITE}" stroke-width="18" fill="none" stroke-linecap="round"/>
  <path d="M62 178 l-26 30 M112 198 l-12 36 M168 198 l12 36 M218 178 l26 30"
        stroke="${WHITE}" stroke-width="14" stroke-linecap="round"/>`);

const icoYawn = ico(`
  <circle cx="140" cy="140" r="104" stroke="${WHITE}" stroke-width="16" fill="none"/>
  <path d="M92 104 q16 -18 32 0 M156 104 q16 -18 32 0" stroke="${WHITE}" stroke-width="14"
        fill="none" stroke-linecap="round"/>
  <ellipse cx="140" cy="182" rx="42" ry="48" fill="${WHITE}"/>`);

const icoNod = ico(`
  <path d="M172 62 a58 58 0 1 0 -8 112 l0 34 a20 20 0 0 0 20 20 l40 0"
        stroke="${WHITE}" stroke-width="16" fill="none" stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="150" cy="112" r="11" fill="${WHITE}"/>
  <path d="M64 150 l0 74 M64 236 l-30 -36 M64 236 l30 -36" stroke="${WHITE}" stroke-width="16"
        fill="none" stroke-linecap="round" stroke-linejoin="round"/>`);

const icoBlink = ico(`
  <path d="M28 140 q112 -86 224 0 q-112 86 -224 0z" stroke="${WHITE}" stroke-width="16" fill="none"
        stroke-linejoin="round"/>
  <circle cx="140" cy="140" r="34" fill="${WHITE}"/>
  <path d="M140 218 l0 34 M62 196 l-22 24 M218 196 l22 24" stroke="${WHITE}" stroke-width="14"
        stroke-linecap="round"/>`);

const icoCamera = ico(`
  <rect x="30" y="76" width="220" height="150" rx="22" stroke="${WHITE}" stroke-width="16" fill="none"/>
  <path d="M104 76 l18 -30 h36 l18 30" stroke="${WHITE}" stroke-width="16" fill="none"
        stroke-linejoin="round"/>
  <circle cx="140" cy="152" r="48" stroke="${WHITE}" stroke-width="16" fill="none"/>
  <circle cx="140" cy="152" r="18" fill="${WHITE}"/>`);

const icoFaceBox = ico(`
  <path d="M34 92 v-38 a20 20 0 0 1 20 -20 h38 M188 34 h38 a20 20 0 0 1 20 20 v38
           M246 188 v38 a20 20 0 0 1 -20 20 h-38 M92 246 h-38 a20 20 0 0 1 -20 -20 v-38"
        stroke="${WHITE}" stroke-width="16" fill="none" stroke-linecap="round"/>
  <circle cx="112" cy="128" r="12" fill="${WHITE}"/>
  <circle cx="168" cy="128" r="12" fill="${WHITE}"/>
  <path d="M108 182 q32 26 64 0" stroke="${WHITE}" stroke-width="14" fill="none" stroke-linecap="round"/>`);

const icoChip = ico(`
  <rect x="76" y="76" width="128" height="128" rx="16" stroke="${WHITE}" stroke-width="16" fill="none"/>
  <rect x="118" y="118" width="44" height="44" rx="7" fill="${WHITE}"/>
  ${[0,1,2].map(i=>`
    <path d="M${106+i*34} 76 v-38" stroke="${WHITE}" stroke-width="14" stroke-linecap="round"/>
    <path d="M${106+i*34} 204 v38" stroke="${WHITE}" stroke-width="14" stroke-linecap="round"/>
    <path d="M76 ${106+i*34} h-38" stroke="${WHITE}" stroke-width="14" stroke-linecap="round"/>
    <path d="M204 ${106+i*34} h38" stroke="${WHITE}" stroke-width="14" stroke-linecap="round"/>`).join('')}`);

const icoClock = ico(`
  <circle cx="140" cy="146" r="102" stroke="${WHITE}" stroke-width="16" fill="none"/>
  <path d="M140 88 v58 l40 26" stroke="${WHITE}" stroke-width="16" fill="none"
        stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M104 26 h72" stroke="${WHITE}" stroke-width="16" stroke-linecap="round"/>`);

const icoSpeaker = ico(`
  <path d="M40 108 h44 l58 -52 v188 l-58 -52 h-44z" stroke="${WHITE}" stroke-width="16" fill="none"
        stroke-linejoin="round"/>
  <path d="M178 104 q30 40 0 84" stroke="${WHITE}" stroke-width="15" fill="none" stroke-linecap="round"/>
  <path d="M216 76 q54 70 0 140" stroke="${WHITE}" stroke-width="15" fill="none" stroke-linecap="round"/>`);

const icoTarget = ico(`
  <circle cx="140" cy="140" r="106" stroke="${WHITE}" stroke-width="16" fill="none"/>
  <circle cx="140" cy="140" r="62" stroke="${WHITE}" stroke-width="16" fill="none"/>
  <circle cx="140" cy="140" r="20" fill="${WHITE}"/>`);

const icoBrain = ico(`
  <path d="M140 56 a44 44 0 0 0 -76 30 a40 40 0 0 0 -6 70 a44 44 0 0 0 82 42z"
        stroke="${WHITE}" stroke-width="15" fill="none" stroke-linejoin="round"/>
  <path d="M140 56 a44 44 0 0 1 76 30 a40 40 0 0 1 6 70 a44 44 0 0 1 -82 42z"
        stroke="${WHITE}" stroke-width="15" fill="none" stroke-linejoin="round"/>
  <path d="M140 56 v186" stroke="${WHITE}" stroke-width="13"/>
  <path d="M100 106 h26 M100 168 h26 M154 132 h26 M154 196 h26" stroke="${WHITE}"
        stroke-width="12" stroke-linecap="round"/>`);

const jobs = [
  ['comp-esp32', esp32, 1400], ['comp-amp', amp, 1400], ['comp-speaker', speaker, 1400],
  ['comp-breadboard', breadboard, 1400], ['comp-power', usbpwr, 1400],
  ['ico-eye-closed', icoEyeClosed, 320], ['ico-yawn', icoYawn, 320], ['ico-nod', icoNod, 320],
  ['ico-blink', icoBlink, 320], ['ico-camera', icoCamera, 320], ['ico-facebox', icoFaceBox, 320],
  ['ico-chip', icoChip, 320], ['ico-clock', icoClock, 320], ['ico-speaker', icoSpeaker, 320],
  ['ico-target', icoTarget, 320], ['ico-brain', icoBrain, 320],
];

// Photo crops of docs/assets/images/drowsy-guard-cover.png (1672 x 941):
// the driver's head and torso for the concept slide, and a portrait slice of the
// whole cabin for the title slide's full-bleed edge.
// JPEG, not PNG: these are photographs, and a lossless copy of each costs the
// deck about two megabytes for no visible gain.
const crops = [
  ['driver-crop.jpg',   { left: 330, top: 105, width: 480, height: 600 },  960, 1200],
  ['cover-portrait.jpg',{ left: 392, top:   0, width: 678, height: 941 }, 1017, 1412],
];

(async () => {
  for (const [name, svg, w] of jobs) {
    await sharp(Buffer.from(svg)).resize({ width: w }).png().toFile(path.join(OUT, name + '.png'));
  }
  for (const [name, box, w, h] of crops) {
    await sharp(COVER).extract(box).resize(w, h).jpeg({ quality: 88, mozjpeg: true })
      .toFile(path.join(OUT, name));
  }
  console.log('generated', jobs.length + crops.length, 'assets ->', OUT);
})();
