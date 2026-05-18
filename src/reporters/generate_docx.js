/**
 * generate_docx.js — 輿情日報 Word 文件產生器 v2（四層框架）
 *
 * 文件結構：
 *   封面頁
 *   一、Layer 1  資料品質儀表板
 *   二、Layer 2  三維情緒矩陣（media / forum / social）
 *   三、Layer 2+ 主目標深度分析（高強度負面文章）
 *   四、Layer 3  競品橫向情報
 *   五、Layer 4  公關策略建議
 *
 * 用法：
 *   node src/reporters/generate_docx.js <report.json> <output.docx>
 */

"use strict";

const fs   = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak,
  ExternalHyperlink, TabStopType, TabStopPosition,
} = require("docx");

// ── 讀取輸入 ──────────────────────────────────────────────────────
const [,, jsonPath, outputPath] = process.argv;
if (!jsonPath || !outputPath) {
  console.error("用法：node generate_docx.js <report.json> <output.docx>");
  process.exit(1);
}

const report = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
const { date, generated_at, primary, competitors } = report;

// ── 顏色配置 ──────────────────────────────────────────────────────
const C = {
  primary:    "CC0000",
  secondary:  "1F3864",
  accent:     "EBF3FB",
  negative:   "C00000",
  positive:   "375623",
  neutral:    "595959",
  border:     "CCCCCC",
  headerBg:   "1F3864",
  headerText: "FFFFFF",
  alertBg:    "FFF2CC",
  alertBorder:"C00000",
  warnBg:     "FFF3CD",
  layer1Bg:   "E8F5E9",  // 資料品質 — 淡綠
  layer2Bg:   "E3F2FD",  // 情緒矩陣 — 淡藍
  layer3Bg:   "F3E5F5",  // 競品 — 淡紫
  layer4Bg:   "FFF8E1",  // PR策略 — 淡黃
  mediaBg:    "E8F5E9",
  forumBg:    "FFF3E0",
  socialBg:   "F3E5F5",
};

// ── 工具函式 ──────────────────────────────────────────────────────
const bdr = (color = C.border) => ({ style: BorderStyle.SINGLE, size: 1, color });
const allBorders = (color) => ({
  top: bdr(color), bottom: bdr(color), left: bdr(color), right: bdr(color),
});
const noBorder  = () => ({ style: BorderStyle.NONE, size: 0, color: "FFFFFF" });
const noBorders = () => ({
  top: noBorder(), bottom: noBorder(), left: noBorder(), right: noBorder(),
});

const channelLabel = (c) => ({
  google_news: "Google News", ptt: "PTT", dcard: "Dcard",
}[c] || c);

const layerLabel = (l) => ({
  media: "媒體層（新聞/公關稿）",
  forum: "論壇層（PTT 真實民意）",
  social: "社群層（Dcard 年輕族群）",
}[l] || l);

const layerBg = (l) => ({ media: C.mediaBg, forum: C.forumBg, social: C.socialBg }[l] || "FFFFFF");

const healthLabel = (pos, neu, neg, total) => {
  if (!total) return { icon: "⚫", text: "無資料", color: C.neutral };
  if (neg > total / 2) return { icon: "🔴", text: "偏負面", color: C.negative };
  if (pos > total / 2) return { icon: "🟢", text: "偏正面", color: C.positive };
  return { icon: "🟡", text: "中立觀察", color: C.neutral };
};

const statusIcon = (s) => ({ ok: "✅", warn: "⚠️", down: "❌" }[s] || "⚠️");
const pct = (n, d) => d > 0 ? `${Math.round(n / d * 100)}%` : "—";

// ── 共用段落元素 ──────────────────────────────────────────────────
function sp(n = 1) {
  return Array.from({ length: n }, () =>
    new Paragraph({ children: [new TextRun("")], spacing: { before: 0, after: 0 } })
  );
}

function hr(color = C.border) {
  return new Paragraph({
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color, space: 1 } },
    spacing: { before: 0, after: 120 },
    children: [],
  });
}

function sectionTitle(num, text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 160 },
    children: [new TextRun({ text: `${num}、${text}`, bold: true, size: 32, color: C.secondary, font: "Arial" })],
  });
}

function subTitle(text, color = C.secondary) {
  return new Paragraph({
    spacing: { before: 240, after: 100 },
    children: [new TextRun({ text, bold: true, size: 24, color, font: "Arial" })],
  });
}

function body(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 60 },
    children: [new TextRun({ text, size: 20, font: "Arial", ...opts })],
  });
}

function indent(text, level = 1, opts = {}) {
  return new Paragraph({
    indent: { left: level * 360 },
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text, size: 20, font: "Arial", ...opts })],
  });
}

// ── 共用 Table 元件 ───────────────────────────────────────────────
function hCell(text, width, opts = {}) {
  return new TableCell({
    borders: allBorders(C.border),
    width: { size: width, type: WidthType.DXA },
    shading: { fill: opts.bg || C.headerBg, type: ShadingType.CLEAR },
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: opts.align || AlignmentType.CENTER,
      children: [new TextRun({
        text, bold: true, size: opts.size || 18,
        color: opts.color || C.headerText, font: "Arial",
      })],
    })],
  });
}

function dCell(text, width, opts = {}) {
  return new TableCell({
    borders: allBorders(opts.borderColor || C.border),
    width: { size: width, type: WidthType.DXA },
    shading: { fill: opts.bg || "FFFFFF", type: ShadingType.CLEAR },
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    verticalAlign: opts.vAlign || VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: opts.align || AlignmentType.CENTER,
      children: [new TextRun({
        text: String(text), size: opts.size || 20, font: "Arial",
        bold: opts.bold, color: opts.color, italics: opts.italics,
      })],
    })],
  });
}

// ── 封面頁 ────────────────────────────────────────────────────────
function buildCoverPage() {
  const hl = primary
    ? healthLabel(primary.positive, primary.neutral, primary.negative, primary.total)
    : { icon: "⚫", text: "無資料", color: C.neutral };

  const reliabilityPct = primary?.data_quality
    ? Math.round(primary.data_quality.reliability_score * 100) : 0;

  return [
    ...sp(4),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 200 },
      children: [new TextRun({ text: "📊", size: 80 })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 160 },
      children: [new TextRun({ text: "台灣便利超商輿情日報", bold: true, size: 52, color: C.secondary, font: "Arial" })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 80 },
      children: [new TextRun({ text: "Taiwan CVS Public Sentiment Daily Report", size: 24, color: C.neutral, font: "Arial" })],
    }),
    hr(C.primary),
    ...sp(1),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 80, after: 80 },
      children: [new TextRun({ text: `報告日期：${date}`, bold: true, size: 26, font: "Arial" })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 80 },
      children: [new TextRun({ text: `產出時間：${generated_at}`, size: 22, color: C.neutral, font: "Arial" })],
    }),
    ...sp(1),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 80, after: 40 },
      children: [new TextRun({ text: `主目標品牌：${primary?.keyword || "—"}　${hl.icon} ${hl.text}`, bold: true, size: 24, font: "Arial", color: hl.color })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 40 },
      children: [new TextRun({ text: `資料可信度：${reliabilityPct}%`, size: 22, font: "Arial", color: reliabilityPct >= 75 ? C.positive : reliabilityPct >= 40 ? C.neutral : C.negative })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 40, after: 0 },
      children: [new TextRun({ text: `競品監測：${(competitors || []).map(c => c.keyword).join("、") || "—"}`, size: 22, color: C.neutral, font: "Arial" })],
    }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

// ── Layer 1：資料品質儀表板 ───────────────────────────────────────
function buildDataQuality() {
  if (!primary?.data_quality) {
    return [sectionTitle("一", "Layer 1：資料品質儀表板"), body("本日無資料品質資訊。")];
  }
  const dq = primary.data_quality;

  // 可信度說明
  const reliabilityPct = Math.round(dq.reliability_score * 100);
  const reliabilityColor = reliabilityPct >= 75 ? C.positive : reliabilityPct >= 40 ? C.neutral : C.negative;

  // 渠道健康狀態表
  const cols = [1800, 1200, 900, 900, 900, 1200, 1500, 960];
  const headers = ["渠道", "資料層", "篇數", "✅正", "⚪中", "🚨負", "狀態", "備註"];

  const rows = dq.channels.map(ch => {
    const statusText = statusIcon(ch.status) + " " + (ch.status === "ok" ? "正常" : ch.status === "warn" ? "低量" : "無資料");
    const pttNote = ch.channel === "ptt" && ch.count > 0
      ? `推${(ch.push_ratio * 100).toFixed(0)}% 噓${(ch.boo_ratio * 100).toFixed(0)}%`
      : ch.status_reason;

    return new TableRow({ children: [
      dCell(ch.label, cols[0], { bold: true }),
      dCell(ch.layer === "media" ? "媒體層" : ch.layer === "forum" ? "論壇層" : "社群層", cols[1], { bg: layerBg(ch.layer) }),
      dCell(ch.count, cols[2]),
      dCell(ch.positive, cols[3], { color: ch.positive > 0 ? C.positive : C.neutral }),
      dCell(ch.neutral, cols[4]),
      dCell(ch.negative, cols[5], { color: ch.negative > 0 ? C.negative : C.neutral }),
      dCell(statusText, cols[6], {
        color: ch.status === "ok" ? C.positive : ch.status === "warn" ? "E65100" : C.negative,
        bold: ch.status !== "ok",
      }),
      dCell(pttNote, cols[7], { size: 16, color: C.neutral, align: AlignmentType.LEFT }),
    ]});
  });

  const elems = [
    sectionTitle("一", "Layer 1：資料品質儀表板"),
    body(`資料可信度評分：${reliabilityPct}%　（媒體層 25% + 論壇層 40% + 社群層 35%）`, { bold: true, color: reliabilityColor }),
    body(`今日總採集篇數：${dq.total} 篇　　三層覆蓋：媒體 ${dq.has_media ? "✅" : "❌"}　論壇 ${dq.has_forum ? "✅" : "❌"}　社群 ${dq.has_social ? "✅" : "❌"}`),
    sp(1)[0],
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: cols,
      rows: [
        new TableRow({ tableHeader: true, children: headers.map((h, i) => hCell(h, cols[i])) }),
        ...rows,
      ],
    }),
  ];

  // 警示區塊
  if (dq.warnings && dq.warnings.length > 0) {
    elems.push(sp(1)[0]);
    elems.push(subTitle("⚠️ 資料缺口警示", C.negative));
    dq.warnings.forEach(w => {
      elems.push(new Paragraph({
        indent: { left: 360 },
        spacing: { before: 60, after: 60 },
        shading: { fill: C.warnBg, type: ShadingType.CLEAR },
        children: [new TextRun({ text: w, size: 20, font: "Arial", color: "8B4513" })],
      }));
    });
    elems.push(body("建議：補充缺失渠道的監測資料後再進行策略決策。", { color: C.neutral, italics: true }));
  }

  elems.push(new Paragraph({ children: [new PageBreak()] }));
  return elems;
}

// ── Layer 2：三維情緒矩陣 ────────────────────────────────────────
function buildSentimentMatrix() {
  if (!primary?.sentiment_3dim) {
    return [sectionTitle("二", "Layer 2：三維情緒矩陣"), body("本日無情緒矩陣資料。")];
  }
  const s3d = primary.sentiment_3dim;
  const layers = [
    { key: "media",  obj: s3d.media,  label: "媒體層（Google News 品牌敘事）", note: "媒體層以公關稿/新聞為主，反映品牌主動推播的敘事，正面比例偏高屬正常現象，關注負面曝光指標。" },
    { key: "forum",  obj: s3d.forum,  label: "論壇層（PTT 真實民意）",             note: "論壇層反映真實使用者情緒，推/噓比例是重要情感信號。" },
    { key: "social", obj: s3d.social, label: "社群層（Dcard 年輕族群）",           note: "社群層反映 18–35 歲消費族群的即時情緒與購物評價。" },
  ];

  const elems = [
    sectionTitle("二", "Layer 2：三維情緒矩陣"),
    body(`三層加權情緒強度：${s3d.overall_avg}　（論壇層 40%、社群層 35%、媒體層 25%）`, { bold: true }),
    body("各層獨立解讀，避免媒體層的正向偏差稀釋論壇/社群的真實民意。", { color: C.neutral, italics: true }),
    sp(1)[0],
  ];

  layers.forEach(({ key, obj, label, note }) => {
    const bg = layerBg(key);

    // 層標題
    elems.push(new Paragraph({
      spacing: { before: 200, after: 80 },
      shading: { fill: bg, type: ShadingType.CLEAR },
      border: { left: { style: BorderStyle.SINGLE, size: 12, color: key === "media" ? C.positive : key === "forum" ? "E65100" : C.primary, space: 4 } },
      indent: { left: 120 },
      children: [new TextRun({ text: `▌ ${label}`, bold: true, size: 24, color: C.secondary, font: "Arial" })],
    }));

    if (!obj || obj.count === 0) {
      elems.push(indent(`❌ 本日無${label.split("（")[0].trim()}資料`, 1, { color: C.negative }));
      elems.push(body(note, { color: C.neutral, italics: true }));
      elems.push(sp(1)[0]);
      return;
    }

    const hl = healthLabel(obj.positive, obj.neutral, obj.negative, obj.count);

    // 情緒統計橫排
    elems.push(body(
      `${hl.icon} ${hl.text}　採集：${obj.count} 篇　正面 ${obj.positive}｜中立 ${obj.neutral}｜負面 ${obj.negative}　強度：${obj.avg_score}`,
      { bold: true, color: hl.color }
    ));

    // PTT 推/噓
    if (key === "forum" && (obj.ptt_push_total + obj.ptt_boo_total) > 0) {
      const totalR = obj.ptt_push_total + obj.ptt_boo_total;
      elems.push(indent(
        `推文：${obj.ptt_push_total}（${pct(obj.ptt_push_total, totalR)}）　噓文：${obj.ptt_boo_total}（${pct(obj.ptt_boo_total, totalR)}）　→文：${obj.ptt_neutral_total}`,
        1, { color: "5D4037" }
      ));
    }

    // 熱門議題
    if (obj.top_themes && obj.top_themes.length > 0) {
      elems.push(indent(`熱門議題：${obj.top_themes.slice(0, 5).join("、")}`, 1, { color: C.neutral }));
    }

    // 層內警報文章
    if (obj.alert_articles && obj.alert_articles.length > 0) {
      elems.push(indent(`🚨 層內高強度負面文章（${obj.alert_articles.length} 篇）：`, 1, { bold: true, color: C.negative }));
      obj.alert_articles.slice(0, 3).forEach((a, i) => {
        elems.push(new Paragraph({
          indent: { left: 720 },
          spacing: { before: 40, after: 40 },
          children: [
            new TextRun({ text: `${i + 1}. `, size: 20, font: "Arial", bold: true }),
            new ExternalHyperlink({
              link: a.url,
              children: [new TextRun({ text: a.title.slice(0, 60), size: 20, style: "Hyperlink", font: "Arial" })],
            }),
            new TextRun({ text: `　強度 ${a.score}`, size: 20, color: C.negative, font: "Arial" }),
          ],
        }));
        if (a.theme) elems.push(indent(`議題：${a.theme}　${a.reason ? "｜" + a.reason.slice(0, 80) : ""}`, 3, { size: 18, color: C.neutral }));
      });
    }

    elems.push(body(note, { color: C.neutral, italics: true }));
    elems.push(sp(1)[0]);
  });

  elems.push(new Paragraph({ children: [new PageBreak()] }));
  return elems;
}

// ── Layer 2+：主目標深度分析（高強度負面 + 代表性文章）──────────
function buildPrimaryDeepDive() {
  if (!primary) return [sectionTitle("三", "主目標深度分析"), body("本日無監測資料。")];

  const hl = healthLabel(primary.positive, primary.neutral, primary.negative, primary.total);

  const elems = [
    sectionTitle("三", `主目標深度分析：${primary.keyword}`),
    body(`輿情健康度：${hl.icon} ${hl.text}　　平均情緒強度：${primary.avg_score}`, { bold: true, color: hl.color }),
    body(`監測總篇數：${primary.total} 篇　　正面：${primary.positive}　中立：${primary.neutral}　負面：${primary.negative}`),
    sp(1)[0],
  ];

  // 熱門議題
  if (primary.top_themes && primary.top_themes.length > 0) {
    elems.push(subTitle("🔥 跨渠道熱門議題排行（前 5）"));
    primary.top_themes.slice(0, 5).forEach((t, i) => {
      elems.push(indent(`${i + 1}. ${t}`, 1));
    });
    elems.push(sp(1)[0]);
  }

  // 高強度負面文章（跨渠道）
  elems.push(subTitle(
    primary.alert_articles && primary.alert_articles.length > 0
      ? `⚠️ 高強度負面警報文章（${primary.alert_count} 篇，風險等級 ≥ 3）`
      : "✅ 本日無高強度負面文章"
    , primary.alert_count > 0 ? C.negative : C.positive
  ));

  if (primary.alert_articles && primary.alert_articles.length > 0) {
    primary.alert_articles.forEach((a, i) => {
      elems.push(new Paragraph({
        spacing: { before: 120, after: 40 },
        shading: { fill: C.alertBg, type: ShadingType.CLEAR },
        border: { left: { style: BorderStyle.SINGLE, size: 8, color: C.alertBorder, space: 4 } },
        indent: { left: 200 },
        children: [
          new TextRun({ text: `${i + 1}. `, bold: true, size: 20, font: "Arial" }),
          new TextRun({ text: a.title, bold: true, size: 20, font: "Arial", color: C.negative }),
        ],
      }));
      elems.push(new Paragraph({
        indent: { left: 360 },
        spacing: { before: 0, after: 40 },
        children: [new TextRun({
          text: `${channelLabel(a.channel)} ／ ${a.source}　強度：${a.score}　主題：${a.theme || "—"}`,
          size: 18, color: C.neutral, font: "Arial",
        })],
      }));
      if (a.reason) {
        elems.push(new Paragraph({
          indent: { left: 360 },
          spacing: { before: 0, after: 40 },
          children: [
            new TextRun({ text: "分析依據：", size: 18, font: "Arial" }),
            new TextRun({ text: a.reason.slice(0, 200), size: 18, color: C.neutral, font: "Arial" }),
          ],
        }));
      }
      elems.push(new Paragraph({
        indent: { left: 360 },
        spacing: { before: 0, after: 120 },
        children: [new ExternalHyperlink({
          link: a.url,
          children: [new TextRun({ text: "🔗 閱讀原文", size: 18, style: "Hyperlink", font: "Arial" })],
        })],
      }));
    });
  } else {
    elems.push(indent("所有渠道情緒均在安全範圍內，品牌形象穩健。", 1, { color: C.positive }));
  }

  // 代表性文章
  if (primary.key_articles && primary.key_articles.length > 0) {
    elems.push(sp(1)[0]);
    elems.push(subTitle("📌 各渠道代表性文章"));
    primary.key_articles.forEach(a => {
      const sentColor = a.sentiment === "負面" ? C.negative : a.sentiment === "正面" ? C.positive : C.neutral;
      elems.push(new Paragraph({
        indent: { left: 360 },
        spacing: { before: 80, after: 20 },
        children: [
          new TextRun({ text: `[${channelLabel(a.channel)}] `, bold: true, size: 20, color: C.secondary, font: "Arial" }),
          new ExternalHyperlink({
            link: a.url,
            children: [new TextRun({ text: a.title.slice(0, 60), size: 20, style: "Hyperlink", font: "Arial" })],
          }),
        ],
      }));
      elems.push(indent(
        `${a.sentiment}（${a.score}）　${a.theme || ""}`,
        2, { size: 18, color: sentColor }
      ));
    });
  }

  elems.push(new Paragraph({ children: [new PageBreak()] }));
  return elems;
}

// ── Layer 3：競品橫向情報 ─────────────────────────────────────────
function buildCompetitorSection() {
  if (!competitors || competitors.length === 0) {
    return [sectionTitle("四", "Layer 3：競品橫向情報"), body("本日競品無監測資料。")];
  }

  const cols = [1400, 800, 700, 700, 700, 1000, 800, 700, 700, 760];
  const headers = ["競品品牌", "總篇", "✅正", "⚪中", "🚨負", "強度", "警報", "媒體", "論壇", "社群"];

  const rows = competitors.map(c => {
    const hl = healthLabel(c.positive, c.neutral, c.negative, c.total);
    return new TableRow({ children: [
      dCell(c.keyword, cols[0], { bold: true }),
      dCell(c.total, cols[1]),
      dCell(c.positive, cols[2], { color: c.positive > 0 ? C.positive : C.neutral }),
      dCell(c.neutral, cols[3]),
      dCell(c.negative, cols[4], { color: c.negative > 0 ? C.negative : C.neutral }),
      dCell(c.avg_score, cols[5], { bold: true, color: hl.color }),
      dCell(c.alert_count, cols[6], { color: c.alert_count > 0 ? C.negative : C.neutral }),
      dCell(c.media_count || 0, cols[7], { bg: C.mediaBg }),
      dCell(c.forum_count || 0, cols[8], { bg: C.forumBg }),
      dCell(c.social_count || 0, cols[9], { bg: C.socialBg }),
    ]});
  });

  // 主目標也加入對照
  const allRows = [];
  if (primary) {
    const dq = primary.data_quality;
    const hl = healthLabel(primary.positive, primary.neutral, primary.negative, primary.total);
    allRows.push(new TableRow({ children: [
      dCell(primary.keyword + " ★", cols[0], { bold: true, bg: C.accent }),
      dCell(primary.total, cols[1], { bg: C.accent }),
      dCell(primary.positive, cols[2], { color: C.positive, bg: C.accent }),
      dCell(primary.neutral, cols[3], { bg: C.accent }),
      dCell(primary.negative, cols[4], { color: primary.negative > 0 ? C.negative : C.neutral, bg: C.accent }),
      dCell(primary.avg_score, cols[5], { bold: true, color: hl.color, bg: C.accent }),
      dCell(primary.alert_count, cols[6], { color: primary.alert_count > 0 ? C.negative : C.neutral, bg: C.accent }),
      dCell((primary.sources?.google_news || 0) + (primary.sources?.news || 0), cols[7], { bg: C.mediaBg }),
      dCell(primary.sources?.ptt || 0, cols[8], { bg: C.forumBg }),
      dCell(primary.sources?.dcard || 0, cols[9], { bg: C.socialBg }),
    ]}));
  }
  allRows.push(...rows);

  const themeSection = competitors.map(c =>
    `${c.keyword}：${(c.top_themes || []).slice(0, 3).join("、") || "—"}`
  ).join("　　");

  return [
    sectionTitle("四", "Layer 3：競品橫向情報"),
    body("三色底色代表資料層：" + [
      "媒體層（綠）", "論壇層（橙）", "社群層（紫）"
    ].join("、"), { color: C.neutral, italics: true }),
    sp(1)[0],
    new Table({
      width: { size: 9360, type: WidthType.DXA },
      columnWidths: cols,
      rows: [
        new TableRow({ tableHeader: true, children: headers.map((h, i) => hCell(h, cols[i])) }),
        ...allRows,
      ],
    }),
    sp(1)[0],
    subTitle("📌 競品熱門議題"),
    body(themeSection || "—"),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

// ── Layer 4：公關策略建議 ────────────────────────────────────────
function buildPRStrategy() {
  if (!primary) return [sectionTitle("五", "Layer 4：公關策略建議"), body("本日無資料。")];

  const trackLabel = primary.pr_track === "A"
    ? "Track A — 危機應對軌 🛡️"
    : "Track B — 品牌進攻軌 🚀";
  const trackColor = primary.pr_track === "A" ? C.negative : C.positive;

  const prLines = (primary.pr_report || "（本次未生成 PR 策略報告）")
    .split("\n").filter(Boolean).slice(0, 60);

  return [
    sectionTitle("五", "Layer 4：公關策略建議"),
    new Paragraph({
      spacing: { before: 120, after: 120 },
      shading: { fill: C.layer4Bg, type: ShadingType.CLEAR },
      border: { left: { style: BorderStyle.SINGLE, size: 16, color: trackColor, space: 4 } },
      indent: { left: 200 },
      children: [new TextRun({ text: trackLabel, bold: true, size: 28, color: trackColor, font: "Arial" })],
    }),
    sp(1)[0],
    ...prLines.map(line => {
      const isHeader = /^[一二三四五六七八九十\d]+[\.、]/.test(line) || line.startsWith("#") || line.startsWith("**");
      const clean = line.replace(/^#+\s*/, "").replace(/\*\*/g, "").trim();
      return isHeader
        ? subTitle(clean, C.secondary)
        : body(clean);
    }),
  ];
}

// ── 組裝文件 ──────────────────────────────────────────────────────
const docContent = [
  ...buildCoverPage(),
  ...buildDataQuality(),
  ...buildSentimentMatrix(),
  ...buildPrimaryDeepDive(),
  ...buildCompetitorSection(),
  ...buildPRStrategy(),
  ...sp(1),
  hr(),
  body(`本報告由台灣便利超商輿情監控系統自動生成　產出時間：${generated_at}`, { color: C.neutral, italics: true }),
];

const doc = new Document({
  styles: {
    default: {
      document: { run: { font: "Arial", size: 20 } },
    },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: C.secondary },
        paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0 },
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },   // A4
        margin: { top: 1134, right: 1134, bottom: 1134, left: 1134 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.primary, space: 4 } },
          children: [
            new TextRun({ text: "台灣便利超商輿情日報", size: 18, color: C.secondary, font: "Arial" }),
            new TextRun({ text: `\t${date}`, size: 18, color: C.neutral, font: "Arial" }),
          ],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: C.border, space: 4 } },
          children: [
            new TextRun({ text: "台灣便利超商輿情監控系統", size: 16, color: C.neutral, font: "Arial" }),
            new TextRun({ text: "\t第 ", size: 16, color: C.neutral, font: "Arial" }),
            new TextRun({ children: [PageNumber.CURRENT], size: 16, font: "Arial" }),
            new TextRun({ text: " 頁", size: 16, color: C.neutral, font: "Arial" }),
          ],
        })],
      }),
    },
    children: docContent,
  }],
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(outputPath, buffer);
  console.log(`✅ Word 日報已產出：${outputPath}`);
}).catch(err => {
  console.error(`❌ 產出失敗：${err.message}`);
  process.exit(1);
});
