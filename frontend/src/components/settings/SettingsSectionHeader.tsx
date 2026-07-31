/**
 * The `.settings-section` card header, and the placeholder cards a Settings
 * page renders while its own fetch is still pending.
 *
 * WHY BOTH LIVE HERE. `StickySectionNav` builds the Settings section rail by
 * querying `.settings-section, [data-settings-section]` inside the content
 * pane, reading each section's heading, and SLUGGING THAT HEADING INTO THE
 * SECTION'S `id` — the id a shared `#settings/<page>?section=…` link names.
 * A page whose sections only mount once its fetch settles therefore has no
 * rail entries and no anchors at all until then: the rail appears from
 * nothing, and a deep link scrolls the reader somewhere they did not ask to
 * go, seconds after they started reading (bead enhancedchannelmanager-b32co;
 * the Stats half of the same defect is commit 22fef24d / bead mch8j).
 *
 * The fix is to render the sections' headers immediately, from a list the page
 * knows before it fetches anything. Both branches take their label from that
 * one list — ONE AUTHORITY PER LABEL. If the loaded branch kept its own
 * literal heading, renaming it would silently move the rail entry AND change
 * the section's id when the fetch settled, which is the same defect wearing a
 * different hat.
 */

export type SettingsSectionMeta = {
  /** Material icon name shown beside the heading. */
  icon: string;
  /**
   * Heading text. The section rail lists it verbatim and slugs the section's
   * anchor id from it, so changing it changes every previously-shared
   * `?section=` link to that section.
   */
  label: string;
};

export function SettingsSectionHeader({ section }: { section: SettingsSectionMeta }) {
  return (
    <div className="settings-section-header">
      <span className="material-icons">{section.icon}</span>
      <h3>{section.label}</h3>
    </div>
  );
}

/**
 * One header-only `.settings-section` per entry, for the window between first
 * paint and the page's fetch settling. They are real sections as far as the
 * rail is concerned, which is the point: the rail is complete and its anchors
 * resolve immediately, and it does not reflow when the real cards replace
 * these.
 */
export function SettingsSectionPlaceholders({ sections }: { sections: readonly SettingsSectionMeta[] }) {
  return (
    <>
      {sections.map((section) => (
        <div className="settings-section" key={section.label} aria-busy="true">
          <SettingsSectionHeader section={section} />
          {/* Visual filler only — `aria-busy` above is what a screen reader
              needs, and repeating "Loading…" once per card is noise. */}
          <p className="form-hint" aria-hidden="true">Loading…</p>
        </div>
      ))}
    </>
  );
}
