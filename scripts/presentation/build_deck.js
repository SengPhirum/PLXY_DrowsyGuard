// Builds docs/assets/documents/DrowsyGuard-Presentation.pptx.
// Run `node gen_assets.js` first - it fills scripts/presentation/build/ with the
// illustrations this script places.
const pptxgen = require('pptxgenjs');
const path = require('path');

const REPO = path.resolve(__dirname, '..', '..');
const A = (f) => path.join(__dirname, 'build', f);

/* ---- palette: night cabin navy + dashboard-telltale amber ---- */
const NAVY_D = '0E1A38';   // deep cabin navy   (dark slide ground)
const NAVY   = '16264F';   // navy              (headings, panels)
const NAVY_M = '223763';   // mid navy          (tiles on dark)
const ICE    = 'C9DCF7';   // ice blue          (support text on dark)
const AMBER  = 'F5A524';   // dashboard amber   (accent)
const AMBER_D= 'B5730A';   // amber, dark       (figures on light)
const LIGHT  = 'F4F6FA';   // light panel ground
const SLATE  = '5D6B87';   // muted body on light
const WHITE  = 'FFFFFF';

const TF = 'Cambria';      // titles  (safe list)
const BF = 'Calibri';      // body    (safe list)

const shadow = (o = {}) => Object.assign(
  { type: 'outer', color: '0B1428', blur: 14, offset: 3, angle: 90, opacity: 0.12 }, o);

const pres = new pptxgen();
pres.layout = 'LAYOUT_WIDE';                 // 13.333 x 7.5
pres.author = 'Drowsy Guard research team';
pres.company = 'RUPP - MITE Cohort 19';
pres.title = 'Drowsy Guard';
const W = 13.333, H = 7.5;

/* helpers ------------------------------------------------------------- */

// amber (or navy) disc with a white icon centred inside it
function iconDisc(slide, { x, y, d, icon, fill = AMBER, iconScale = 0.52 }) {
  slide.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill } });
  const s = d * iconScale;
  slide.addImage({ path: A(icon), x: x + (d - s) / 2, y: y + (d - s) / 2, w: s, h: s });
}

function slideTitle(slide, text, { color = NAVY, y = 0.44, sub = null, subColor = SLATE } = {}) {
  slide.addText(text, {
    x: 0.6, y, w: W - 1.2, h: 0.62, isTextBox: true, margin: 0,
    fontFace: TF, fontSize: 30, bold: true, color, valign: 'middle',
  });
  if (sub) slide.addText(sub, {
    x: 0.6, y: y + 0.62, w: W - 1.2, h: 0.36, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 13.5, color: subColor, valign: 'middle',
  });
}

/* ===================================================================== */
/* 1 - TITLE                                                              */
/* ===================================================================== */
{
  const s = pres.addSlide();
  s.background = { color: NAVY_D };
  s.addImage({ path: A('cover-portrait.jpg'), x: 7.93, y: 0, w: 5.403, h: 7.5 });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.75, y: 0.82, w: 3.62, h: 0.42, rectRadius: 0.21,
    fill: { color: NAVY_M }, line: { color: NAVY_M },
  });
  s.addShape(pres.ShapeType.ellipse, { x: 0.98, y: 0.965, w: 0.15, h: 0.15, fill: { color: AMBER }, line: { color: AMBER } });
  s.addText('IoT: Technology and Application', {
    x: 1.22, y: 0.82, w: 3.1, h: 0.42, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 11.5, color: ICE, valign: 'middle', charSpacing: 0.6,
  });

  s.addText('Drowsy Guard', {
    x: 0.72, y: 1.42, w: 7.0, h: 1.15, isTextBox: true, margin: 0,
    fontFace: TF, fontSize: 54, bold: true, color: WHITE, valign: 'middle',
  });
  s.addText('A low-cost IoT system for driver drowsiness detection', {
    x: 0.75, y: 2.60, w: 7.05, h: 0.44, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 17, color: AMBER, valign: 'middle',
  });
  s.addText(
    'An ESP32-S3 vision device that watches for closed eyes, long blinks, yawning and head ' +
    'nodding, then speaks a warning - on the device, with no cloud and no internet.',
    { x: 0.75, y: 3.16, w: 6.9, h: 0.78, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 13, color: ICE, lineSpacing: 20 });

  const meta = [
    ['Programme', 'Master of IT Engineering (MITE), Cohort 19'],
    ['Institution', 'Royal University of Phnom Penh - evening, weekdays'],
    ['Lecturer', 'Dr. Chey Chan Oeurn'],
  ];
  meta.forEach(([k, v], i) => {
    const y = 4.18 + i * 0.46;
    s.addText(k, { x: 0.75, y, w: 1.35, h: 0.34, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 11.5, color: '7C8DB0', valign: 'middle' });
    s.addText(v, { x: 2.05, y, w: 5.5, h: 0.34, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 12.5, bold: true, color: WHITE, valign: 'middle' });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.75, y: 5.72, w: 6.72, h: 0.95, rectRadius: 0.1,
    fill: { color: NAVY }, line: { color: NAVY },
  });
  s.addText([
    { text: 'Team   ', options: { fontSize: 11, color: '7C8DB0', bold: true } },
    { text: 'Seng Phirum', options: { fontSize: 12.5, color: AMBER, bold: true } },
    { text: '  (leader)   ·   Seang Chanviwath  ·  Suon Mesa  ·  Theng Rathrongroeung  ·  Lov Kimheng',
      options: { fontSize: 12.5, color: WHITE } },
  ], { x: 1.0, y: 5.72, w: 6.25, h: 0.95, isTextBox: true, margin: 0, fontFace: BF, valign: 'middle' });

  s.addText('Research proposal presentation   ·   August 2026', {
    x: 0.75, y: 6.82, w: 6.7, h: 0.34, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 10.5, color: '6C7C9E', valign: 'middle',
  });

  s.addNotes(
    'Drowsy Guard - MITE Cohort 19 research proposal.\n' +
    'One sentence framing: many older vehicles in Cambodia have no driver monitoring at all, ' +
    'and a USD 27 device that runs entirely offline can retrofit one.\n' +
    'Say up front that this is a research prototype, not a certified safety device.');
}

/* ===================================================================== */
/* 2 - CONCEPT                                                            */
/* ===================================================================== */
{
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  slideTitle(s, 'Concept - four visible signs, confirmed over time', {
    sub: 'A single closed-eye frame is not drowsiness. Every sign is measured as a duration.',
  });

  const IX = 0.6, IY = 1.5, IW = 3.85, IH = 4.81;
  s.addImage({ path: A('driver-crop.jpg'), x: IX, y: IY, w: IW, h: IH, rounding: false });

  // anchor markers on the face: eyes, mouth, head
  const marks = [
    { u: 0.578, v: 0.346, dx:  0.46, dy: -0.34, icon: 'ico-eye-closed.png' }, // closed eye
    { u: 0.547, v: 0.482, dx:  0.52, dy:  0.16, icon: 'ico-yawn.png' },       // open mouth
    { u: 0.440, v: 0.125, dx: -0.34, dy: -0.10, icon: 'ico-nod.png' },        // head pitching down
  ];
  marks.forEach(({ u, v, dx, dy, icon }) => {
    const cx = IX + u * IW, cy = IY + v * IH;
    s.addShape(pres.ShapeType.ellipse, {
      x: cx - 0.085, y: cy - 0.085, w: 0.17, h: 0.17, fill: { color: WHITE }, line: { color: WHITE } });
    s.addShape(pres.ShapeType.ellipse, {
      x: cx - 0.055, y: cy - 0.055, w: 0.11, h: 0.11, fill: { color: AMBER }, line: { color: AMBER } });
    const d = 0.44, bx = cx + dx - d / 2, by = cy + dy - d / 2;
    s.addShape(pres.ShapeType.ellipse, {
      x: bx - 0.055, y: by - 0.055, w: d + 0.11, h: d + 0.11,
      fill: { color: WHITE }, line: { color: WHITE } });
    iconDisc(s, { x: bx, y: by, d, icon, iconScale: 0.56 });
  });

  const cues = [
    ['ico-eye-closed.png', 'Eye closed',      'Sustained closure - a blink that never comes back.', '≥ 1.0 s', 'microsleep'],
    ['ico-blink.png',      'Long / slow blink','The eyelid slows down before the driver does.',      '> 0.4 s',  'per blink'],
    ['ico-yawn.png',       'Yawning',          'Mouth held open far longer than speech or chewing.', '≥ 1.2 s', 'mouth open'],
    ['ico-nod.png',        'Head nodding',     'Down-and-back, against the driver’s own baseline.', '0.3 - 1.5 s', 'per nod'],
  ];
  const CX = 4.95, CW = 7.78, CH = 1.13, GAP = 0.145;
  cues.forEach(([icon, name, desc, fig, unit], i) => {
    const y = 1.5 + i * (CH + GAP);
    s.addShape(pres.ShapeType.roundRect, {
      x: CX, y, w: CW, h: CH, rectRadius: 0.09,
      fill: { color: WHITE }, line: { color: 'DFE5F0', width: 1 }, shadow: shadow(),
    });
    iconDisc(s, { x: CX + 0.3, y: y + (CH - 0.62) / 2, d: 0.62, icon });
    s.addText(name, {
      x: CX + 1.12, y: y + 0.17, w: 4.0, h: 0.35, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 15.5, bold: true, color: NAVY, valign: 'middle',
    });
    s.addText(desc, {
      x: CX + 1.12, y: y + 0.53, w: 4.3, h: 0.42, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 11.5, color: SLATE, valign: 'top',
    });
    s.addText(fig, {
      x: CX + 5.55, y: y + 0.16, w: 2.05, h: 0.5, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: fig.length > 8 ? 18 : 23, bold: true, color: AMBER_D,
      align: 'right', valign: 'middle',
    });
    s.addText(unit, {
      x: CX + 5.55, y: y + 0.65, w: 2.05, h: 0.3, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 10.5, color: SLATE, align: 'right', valign: 'middle',
    });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.6, y: 6.52, w: 12.13, h: 0.72, rectRadius: 0.1,
    fill: { color: NAVY }, line: { color: NAVY },
  });
  s.addText([
    { text: 'One sign is never enough.  ', options: { bold: true, color: AMBER } },
    { text: 'Risk fuses all four - PERCLOS 55%  ·  long blink 20%  ·  yawn 15%  ·  head nod 10% - and a sneeze suppresses the alert instead of firing it.',
      options: { color: ICE } },
  ], { x: 0.95, y: 6.52, w: 11.5, h: 0.72, isTextBox: true, margin: 0, fontFace: BF, fontSize: 12.5, valign: 'middle' });

  s.addNotes(
    'The whole design rests on this slide: drowsiness is a duration, not a frame.\n' +
    'Thresholds shown are the literature-informed defaults now in the firmware ' +
    '(MICROSLEEP_MIN_S 1.0, BLINK_MAX_S 0.4, YAWN_MIN_S 1.2, NOD 0.3-1.5 s). ' +
    'They are unit-tested on synthetic traces but not yet tuned on labelled video - say so if asked.\n' +
    'Sneeze detection exists to SUPPRESS a false alert: a sneeze shuts the eyes for about a second ' +
    'with a head jerk, and would otherwise read as a microsleep.');
}

/* ===================================================================== */
/* 3 - FLOW                                                               */
/* ===================================================================== */
{
  const s = pres.addSlide();
  s.background = { color: WHITE };
  slideTitle(s, 'Flow - how one alert is made', {
    sub: 'Five steps, all of them on the device. Nothing in the alert path touches the network.',
  });

  const steps = [
    ['ico-camera.png',  'See',     'The OV5640 captures the driver’s face at 240 × 240.'],
    ['ico-facebox.png', 'Find',    'Face plus five landmarks. Detect every 3rd frame, track in between.'],
    ['ico-chip.png',    'Measure', 'Eye model every frame → PERCLOS. Yawn, nod and roll come from the landmarks.'],
    ['ico-clock.png',   'Confirm', 'Risk must hold ≥ 0.72 for 8 frames in a row. One frame never alerts.'],
    ['ico-speaker.png', 'Warn',    'A spoken warning names the cause: Drowsy, Microsleep, Yawning or HeadNod.'],
  ];
  const SW = 2.2, SG = 0.3, SX = 0.62, SY = 1.62, SH = 3.28;
  steps.forEach(([icon, name, body], i) => {
    const x = SX + i * (SW + SG);
    const last = i === steps.length - 1;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: SY, w: SW, h: SH, rectRadius: 0.11,
      fill: { color: last ? AMBER : LIGHT }, line: { color: last ? AMBER : 'DFE5F0', width: 1 },
      shadow: shadow({ opacity: last ? 0.18 : 0.09 }),
    });
    iconDisc(s, { x: x + (SW - 0.86) / 2, y: SY + 0.34, d: 0.86, icon, fill: last ? NAVY_D : NAVY, iconScale: 0.5 });
    s.addText(String(i + 1).padStart(2, '0'), {
      x: x + 0.16, y: SY + 0.16, w: 0.5, h: 0.3, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 11, bold: true, color: last ? '7A4F02' : 'A9B5CC', valign: 'middle',
    });
    s.addText(name, {
      x: x + 0.12, y: SY + 1.36, w: SW - 0.24, h: 0.42, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 18, bold: true, color: last ? '4A3000' : NAVY, align: 'center', valign: 'middle',
    });
    s.addText(body, {
      x: x + 0.2, y: SY + 1.84, w: SW - 0.4, h: 1.5, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 11.5, color: last ? '5A3D00' : SLATE, align: 'center', valign: 'top', lineSpacing: 16,
    });
    if (!last) s.addShape(pres.ShapeType.rightArrow, {
      x: x + SW + 0.055, y: SY + SH / 2 - 0.11, w: 0.19, h: 0.22,
      fill: { color: 'B9C4D8' }, line: { color: 'B9C4D8' },
    });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.62, y: 5.22, w: 7.55, h: 1.0, rectRadius: 0.1, fill: { color: NAVY }, line: { color: NAVY },
  });
  s.addShape(pres.ShapeType.ellipse, { x: 0.95, y: 5.51, w: 0.42, h: 0.42, fill: { color: AMBER }, line: { color: AMBER } });
  s.addImage({ path: A('ico-target.png'), x: 1.055, y: 5.615, w: 0.21, h: 0.21 });
  s.addText([
    { text: 'The alert never needs the internet.  ', options: { bold: true, color: AMBER } },
    { text: 'Capture, inference and warning all run on the ESP32-S3.', options: { color: ICE } },
  ], { x: 1.52, y: 5.22, w: 6.4, h: 1.0, isTextBox: true, margin: 0, fontFace: BF, fontSize: 12.5, valign: 'middle' });

  s.addShape(pres.ShapeType.roundRect, {
    x: 8.37, y: 5.22, w: 4.36, h: 1.0, rectRadius: 0.1,
    fill: { color: 'E7EEFA' }, line: { color: 'D3DEF2', width: 1 },
  });
  s.addText([
    { text: 'Wi-Fi is diagnostics only:  ', options: { bold: true, color: NAVY } },
    { text: 'risk, PERCLOS, event log and a speaker self-test in a browser.', options: { color: SLATE } },
  ], { x: 8.65, y: 5.22, w: 3.85, h: 1.0, isTextBox: true, margin: 0, fontFace: BF, fontSize: 11.5, valign: 'middle' });

  s.addText('Measured on hardware:  19.7 fps with no face in view, 10.7 - 13.6 fps while a face is tracked.', {
    x: 0.62, y: 6.48, w: 12.1, h: 0.36, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 11, italic: true, color: SLATE, valign: 'middle',
  });

  s.addNotes(
    'Walk left to right once, then make the two points that matter to a reviewer.\n' +
    '1) Step 4 is the research contribution: a temporal filter, not a single-frame alarm. ' +
    'Risk >= 0.72 sustained for 8 consecutive frames, then a 60-frame cooldown.\n' +
    '2) The preview is deliberately never load-bearing - if Wi-Fi dies, diagnostics degrade, safety does not.');
}

/* ===================================================================== */
/* 4 - EQUIPMENT                                                          */
/* ===================================================================== */
{
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  slideTitle(s, 'Equipment - five parts, about US$ 27', {
    sub: 'Everything is off-the-shelf and locally available. No custom board, no cloud subscription.',
  });

  const parts = [
    ['comp-esp32.png',      'ESP32-S3 N16R8\ncamera board', 'Edge AI, Wi-Fi and the OV5640 sensor, all on one board.',        '$7.50'],
    ['comp-amp.png',        'MAX98357A\nI2S amplifier',    'Turns the decision into sound the driver can actually hear.',    '$2.00'],
    ['comp-speaker.png',    'Speaker\n4 Ω / 3 W',          'The only output the driver perceives - there is no screen.',     '$0.75'],
    ['comp-breadboard.png', 'Breadboard\n+ jumper wires',   'MB102, 830 tie-points. Solderless, so the build can be redone in minutes.', '$3.50'],
    ['comp-power.png',      '5 V USB supply\n+ USB-C cable','Bench power and firmware flashing over one cable.',              '$3.50'],
  ];
  const PW = 2.2, PG = 0.3, PX = 0.62, PY = 1.62, PH = 3.72;
  parts.forEach(([img, name, use, price], i) => {
    const x = PX + i * (PW + PG);
    s.addShape(pres.ShapeType.roundRect, {
      x, y: PY, w: PW, h: PH, rectRadius: 0.11,
      fill: { color: WHITE }, line: { color: 'DFE5F0', width: 1 }, shadow: shadow(),
    });
    s.addImage({ path: A(img), x: x + 0.15, y: PY + 0.22, w: PW - 0.3, h: (PW - 0.3) * 0.64 });
    s.addText(name, {
      x: x + 0.1, y: PY + 1.62, w: PW - 0.2, h: 0.62, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 13.5, bold: true, color: NAVY, align: 'center', valign: 'top', lineSpacing: 17,
    });
    s.addText(use, {
      x: x + 0.14, y: PY + 2.28, w: PW - 0.28, h: 0.82, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 10.5, color: SLATE, align: 'center', valign: 'top', lineSpacing: 14,
    });
    s.addShape(pres.ShapeType.roundRect, {
      x: x + (PW - 1.05) / 2, y: PY + PH - 0.62, w: 1.05, h: 0.42, rectRadius: 0.21,
      fill: { color: 'FCEFD6' }, line: { color: 'FCEFD6' },
    });
    s.addText(price, {
      x: x + (PW - 1.05) / 2, y: PY + PH - 0.62, w: 1.05, h: 0.42, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 13, bold: true, color: AMBER_D, align: 'center', valign: 'middle',
    });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.62, y: 5.62, w: 12.11, h: 1.12, rectRadius: 0.11, fill: { color: NAVY }, line: { color: NAVY },
  });
  s.addText('US$ 27.25', {
    x: 0.95, y: 5.62, w: 2.6, h: 1.12, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 26, bold: true, color: AMBER, valign: 'middle',
  });
  s.addText([
    { text: 'complete research prototype.  ', options: { bold: true, color: WHITE } },
    { text: 'The five parts above come to US$ 17.25; the balance is an inline USB power meter and enclosure / mount materials for the tests. Laptop and phone are excluded - the team already owns them.',
      options: { color: ICE } },
  ], { x: 3.62, y: 5.62, w: 8.83, h: 1.12, isTextBox: true, margin: 0, fontFace: BF, fontSize: 12, valign: 'middle', lineSpacing: 17 });

  s.addText('Cost basis: Appendix A of the project proposal.', {
    x: 0.62, y: 6.86, w: 12.1, h: 0.32, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 10, italic: true, color: '8492AC', valign: 'middle',
  });

  s.addNotes(
    'The cost is part of the argument, not a footnote: the target is a retrofit for older vehicles ' +
    'that have no driver monitoring, so the whole device has to stay inside a price a driver would pay.\n' +
    'One honest note if asked: the board was advertised with an OV3660 and shipped with an OV5640. ' +
    'Same DVP pin map as the ESP32-S3-EYE, so the wiring and the published frame budget carried over unchanged.');
}

/* ===================================================================== */
/* 5 - TEMPORARY RESULTS                                                  */
/* ===================================================================== */
{
  const s = pres.addSlide();
  s.background = { color: NAVY_D };
  slideTitle(s, 'Where it is now - first hardware run', {
    color: WHITE, subColor: '8FA3C7',
    sub: 'Flashed to the board on 23 August 2026. These are measured numbers, not estimates.',
  });

  const stats = [
    ['19.7', 'fps',  'Frame rate, no face in view'],
    ['10.7 - 13.6', '', 'fps while tracking a face'],
    ['15.7', 'ms',   'One eye inference, on the board'],
    ['39', 'ms',     'Full-frame face detection'],
    ['20 / 20', '',  'Detections found, score 1.00'],
    ['0.00 - 0.22', '', 'PERCLOS at rest - first live reading'],
  ];
  const TW = 2.13, TG = 0.2, TX = 0.62, TY = 1.66, TH = 1.44;
  stats.forEach(([fig, unit, label], i) => {
    const x = TX + (i % 3) * (TW + TG), y = TY + Math.floor(i / 3) * (TH + 0.2);
    s.addShape(pres.ShapeType.roundRect, {
      x, y, w: TW, h: TH, rectRadius: 0.1, fill: { color: NAVY_M }, line: { color: '2E4470', width: 1 },
    });
    s.addText([
      { text: fig, options: { fontSize: fig.length > 6 ? 20 : 25, bold: true, color: AMBER } },
      { text: unit ? ' ' + unit : '', options: { fontSize: 12, bold: true, color: 'C79544' } },
    ], { x: x + 0.18, y: y + 0.16, w: TW - 0.36, h: 0.52, isTextBox: true, margin: 0, fontFace: BF, valign: 'middle' });
    s.addText(label, {
      x: x + 0.18, y: y + 0.72, w: TW - 0.36, h: 0.58, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 10.5, color: ICE, valign: 'top', lineSpacing: 14,
    });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: TX, y: 4.94, w: 6.79, h: 1.86, rectRadius: 0.1, fill: { color: NAVY }, line: { color: '2E4470', width: 1 },
  });
  s.addText([
    { text: 'Working on hardware:  ', options: { bold: true, color: '5FD3B2' } },
    { text: '8 MB PSRAM, camera, I2S chime, its own Wi-Fi access point, face detector and live landmark overlay.\n', options: { color: ICE } },
    { text: 'Not yet exercised:  ', options: { bold: true, color: AMBER } },
    { text: 'an alert firing end to end, the cues against a real yawn or nod, and heap behaviour over hours.\n', options: { color: ICE } },
    { text: 'The open gap:  ', options: { bold: true, color: 'FF8E7F' } },
    { text: 'the eye model runs, but scores AUC 0.62 on visible-light eyes. Accuracy is the remaining problem.', options: { color: ICE } },
  ], { x: TX + 0.28, y: 4.94, w: 6.25, h: 1.86, isTextBox: true, margin: 0, fontFace: BF, fontSize: 11.5, valign: 'middle', lineSpacing: 17 });

  // screenshot slot
  const GX = 7.78, GY = 1.66, GW = 4.95, GH = 5.14;
  s.addShape(pres.ShapeType.roundRect, {
    x: GX, y: GY, w: GW, h: GH, rectRadius: 0.1,
    fill: { color: '132349' }, line: { color: AMBER, width: 1.5, dashType: 'dash' },
  });
  iconDisc(s, { x: GX + GW / 2 - 0.45, y: GY + GH / 2 - 1.15, d: 0.9, icon: 'ico-camera.png', iconScale: 0.5 });
  s.addText('Live preview screenshot', {
    x: GX + 0.3, y: GY + GH / 2 - 0.1, w: GW - 0.6, h: 0.42, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 15, bold: true, color: WHITE, align: 'center', valign: 'middle',
  });
  s.addText('Face box, eye and mouth landmarks, risk bar,\nPERCLOS and the event log at 192.168.4.1\n\n[ drop the captured screenshot here ]', {
    x: GX + 0.35, y: GY + GH / 2 + 0.32, w: GW - 0.7, h: 1.3, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 11.5, color: '8FA3C7', align: 'center', valign: 'top', lineSpacing: 17,
  });

  s.addNotes(
    'Be precise about what is proven and what is not - a reviewer will ask.\n' +
    'Proven: the board boots and the pipeline runs at a usable frame rate with the eye model bound.\n' +
    'Two real bugs were only findable on hardware: the sensor is mounted upside down, so vflip left the ' +
    'frame horizontally mirrored, which reversed the eye pair and inverted every vertical cue - the yawn cue ' +
    'had never been able to fire. Fixed; the detection gate now reports ok on every detection.\n' +
    'ACTION: replace the dashed panel with the live screenshot before presenting.');
}

/* ===================================================================== */
/* 6 - CONCLUSION                                                         */
/* ===================================================================== */
{
  const s = pres.addSlide();
  s.background = { color: NAVY_D };
  slideTitle(s, 'Conclusion - the effort now goes into the model', { color: WHITE });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.62, y: 1.28, w: 12.11, h: 0.92, rectRadius: 0.1, fill: { color: NAVY }, line: { color: NAVY },
  });
  s.addShape(pres.ShapeType.ellipse, { x: 0.94, y: 1.51, w: 0.46, h: 0.46, fill: { color: AMBER }, line: { color: AMBER } });
  s.addImage({ path: A('ico-brain.png'), x: 1.055, y: 1.625, w: 0.23, h: 0.23 });
  s.addText([
    { text: 'The team keeps its effort on training the lightweight on-device model. ', options: { bold: true, color: AMBER } },
    { text: 'Camera, audio, Wi-Fi and face tracking are proven - every remaining risk in this project sits inside one small model.',
      options: { color: ICE } },
  ], { x: 1.56, y: 1.28, w: 10.9, h: 0.92, isTextBox: true, margin: 0, fontFace: BF, fontSize: 13.5, valign: 'middle' });

  const pts = [
    ['ico-chip.png', 'Keep it light, by necessity',
      'The eye model is 11,250 parameters and 46 KB, and runs in 15.7 ms on the board. The most accurate public drowsiness models are 70 - 343 MB - they simply cannot run on an ESP32-S3. Small is the constraint, so small has to be made accurate.'],
    ['ico-target.png', 'Train it in the right domain',
      'The base model was trained on infrared eyes and scores only AUC 0.62 on our visible-light crops. Grayscale, histogram equalisation and CLAHE did not recover it. The next step is fine-tuning on visible-light eye-state labels, split by subject.'],
    ['ico-facebox.png', 'Judge it per driver',
      'Never on an average. An earlier whole-face model looked strong at 0.81 overall but fell to 0.57 on drivers it had never seen - it had learned who the driver was, not whether the eyes were shut. Subject-independent splits only, from here on.'],
  ];
  const QW = 3.85, QG = 0.36, QX = 0.62, QY = 2.46, QH = 2.92;
  pts.forEach(([icon, head, body], i) => {
    const x = QX + i * (QW + QG);
    s.addShape(pres.ShapeType.roundRect, {
      x, y: QY, w: QW, h: QH, rectRadius: 0.11, fill: { color: NAVY }, line: { color: '2E4470', width: 1 },
    });
    iconDisc(s, { x: x + 0.32, y: QY + 0.32, d: 0.68, icon, iconScale: 0.5 });
    s.addText(head, {
      x: x + 1.16, y: QY + 0.3, w: QW - 1.48, h: 0.72, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 15, bold: true, color: WHITE, valign: 'middle', lineSpacing: 19,
    });
    s.addText(body, {
      x: x + 0.34, y: QY + 1.14, w: QW - 0.68, h: 1.6, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 11, color: ICE, valign: 'top', lineSpacing: 16,
    });
  });

  s.addText('The targets that decide whether it worked', {
    x: 0.66, y: 5.6, w: 6.0, h: 0.32, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 11, color: '8FA3C7', valign: 'middle', charSpacing: 0.5,
  });
  const kpis = ['F1 ≥ 0.80', 'Recall ≥ 0.85', '≤ 1 false alert / hour', 'Alert within 2 s', '≥ 10 fps on device'];
  const KW = 2.35, KG = 0.11;
  kpis.forEach((k, i) => {
    const x = 0.62 + i * (KW + KG);
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 5.99, w: KW, h: 0.62, rectRadius: 0.14, fill: { color: '1D3160' }, line: { color: AMBER, width: 1 },
    });
    s.addText(k, {
      x, y: 5.99, w: KW, h: 0.62, isTextBox: true, margin: 0,
      fontFace: BF, fontSize: 12.5, bold: true, color: AMBER, align: 'center', valign: 'middle',
    });
  });

  s.addText('Drowsy Guard is a research prototype, not a certified automotive safety device. It is not a substitute for rest.', {
    x: 0.62, y: 6.82, w: 12.11, h: 0.36, isTextBox: true, margin: 0,
    fontFace: BF, fontSize: 10.5, italic: true, color: '7C8DB0', valign: 'middle',
  });

  s.addNotes(
    'Close on the commitment: the team keeps pushing on training the light model, because that is ' +
    'the only thing between this and a working alarm.\n' +
    'The three points are the method, not a wish list: keep the model small enough for the board, ' +
    'train it in visible light rather than infrared, and validate per driver on held-out subjects.\n' +
    'End with the honest boundary - research prototype, controlled conditions only, no drowsy driving on public roads.');
}

pres.writeFile({ fileName: path.join(REPO, 'docs', 'assets', 'documents', 'DrowsyGuard-Presentation.pptx') })
  .then(f => console.log('wrote', f));
