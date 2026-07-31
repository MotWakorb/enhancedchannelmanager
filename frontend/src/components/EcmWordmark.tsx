/**
 * ECM stacked wordmark: italic "Enhanced" over a rule over "CHANNEL MANAGER".
 *
 * Both lines are set with `textLength` and `lengthAdjust="spacing"`, which
 * reaches the target width by adjusting tracking rather than stretching the
 * glyphs. That is what keeps the two lines exactly flush with each other and
 * with the rule on any machine, even where the system font differs — a
 * hand-tuned letter-spacing value would drift between platforms.
 *
 * Caps on the second line are not only a style choice: capitals are naturally
 * wider than title case, so the line reaches the target width with less forced
 * tracking and the letters sit more comfortably.
 */

/** PO-selected proportions. All values are in the SVG's own units. */
const WORDMARK = {
  /** Both lines and the rule share this width. */
  width: 99,
  padding: 1,
  line1: { size: 20.5, weight: 750 },
  rule: { thickness: 0.5, spaceAbove: 1.25, spaceBelow: 1.25 },
  line2: { size: 10.25, weight: 550 },
} as const;

/** Cap height as a fraction of font size, used to place the second line by its
 *  cap top rather than its baseline — the rule should sit a set distance above
 *  the capitals, not above a baseline the eye cannot see. */
const CAP_RATIO = 0.72;

/** Ascender height as a fraction of font size. "Enhanced" has ascenders (h, d)
 *  that reach slightly above cap height, and that is the true top of the ink. */
const ASCENT_RATIO = 0.76;

/** Symmetric slack above and below the ink so nothing clips if the system font
 *  runs taller than the ratios above. Being symmetric, it does not disturb the
 *  vertical centring. */
const BLEED = 0.6;

const { width, padding, line1, rule, line2 } = WORDMARK;
const LINE1_BASELINE = line1.size;
const RULE_Y = LINE1_BASELINE + rule.spaceAbove;
const LINE2_BASELINE = RULE_Y + rule.thickness + rule.spaceBelow + CAP_RATIO * line2.size;

/**
 * The viewBox is cropped to the ink rather than starting at zero. Text is
 * positioned by baseline, so a zero-origin box carries ~5 units of dead space
 * above "Enhanced" and almost none below the second line — which makes the
 * wordmark sit visibly low when the brand row centres it against the mark.
 * Cropping to the ink means centring the box centres what you can see.
 * Capitals have no descenders, so the second line's baseline is the true floor.
 */
const INK_TOP = Number((LINE1_BASELINE - line1.size * ASCENT_RATIO - BLEED).toFixed(3));
const INK_BOTTOM = Number((LINE2_BASELINE + BLEED).toFixed(3));
/** Extra room on the right only: the italic "d" leans past its advance width. */
const VIEW_WIDTH = width + padding * 2 + 2;
const VIEW_HEIGHT = Number((INK_BOTTOM - INK_TOP).toFixed(3));

export function EcmWordmark({ className, scale = 1 }: { className?: string; scale?: number }) {
  return (
    <svg
      className={className}
      width={VIEW_WIDTH * scale}
      height={VIEW_HEIGHT * scale}
      viewBox={`0 ${INK_TOP} ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
      role="img"
      aria-label="Enhanced Channel Manager"
    >
      <text
        x={padding}
        y={LINE1_BASELINE}
        textLength={width}
        lengthAdjust="spacing"
        fontSize={line1.size}
        fontWeight={line1.weight}
        fontStyle="italic"
        fill="currentColor"
      >
        Enhanced
      </text>
      <rect
        x={padding}
        y={RULE_Y}
        width={width}
        height={rule.thickness}
        fill="currentColor"
      />
      <text
        x={padding}
        y={LINE2_BASELINE}
        textLength={width}
        lengthAdjust="spacing"
        fontSize={line2.size}
        fontWeight={line2.weight}
        fill="currentColor"
        opacity="0.85"
      >
        CHANNEL MANAGER
      </text>
    </svg>
  );
}
