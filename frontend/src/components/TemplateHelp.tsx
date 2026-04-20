/**
 * Collapsible syntax reference for the dummy EPG template engine.
 *
 * Kept tight: each row is a compact example + one-line explanation so the
 * reader can scan rather than read. Section groupings mirror the engine's
 * mental model: placeholders, pipes, lookups, conditionals.
 */
import { memo } from 'react';

import './TemplateHelp.css';

interface TemplateHelpProps {
  isOpen: boolean;
  onToggle: () => void;
}

export const TemplateHelp = memo(function TemplateHelp({ isOpen, onToggle }: TemplateHelpProps) {
  return (
    <div className="template-help">
      <button type="button" className="template-help-toggle" onClick={onToggle}>
        <span className="material-icons">{isOpen ? 'expand_less' : 'expand_more'}</span>
        <span>Template Syntax Reference</span>
      </button>
      {isOpen && (
        <div className="template-help-body">
          <section>
            <h4>Placeholders</h4>
            <dl>
              <dt><code>{'{name}'}</code></dt>
              <dd>Insert the value of a named regex group.</dd>
              <dt><code>{'{name_normalize}'}</code></dt>
              <dd>Legacy shortcut: lowercase and strip non-alphanumeric characters.</dd>
            </dl>
          </section>

          <section>
            <h4>Pipe transforms</h4>
            <p className="template-help-hint">
              Chain left to right with <code>|</code>. Each pipe receives the previous output.
            </p>
            <dl>
              <dt><code>{'|uppercase'}</code> · <code>{'|lowercase'}</code> · <code>{'|titlecase'}</code></dt>
              <dd>Change case.</dd>
              <dt><code>{'|trim'}</code></dt>
              <dd>Strip leading &amp; trailing whitespace.</dd>
              <dt><code>{'|strip:<chars>'}</code></dt>
              <dd>Strip any of the given characters from both ends (e.g. <code>|strip:-</code>).</dd>
              <dt><code>{'|replace:<from>:<to>'}</code></dt>
              <dd>Replace every occurrence; <code>to</code> may be empty (<code>|replace:x:</code> deletes every <code>x</code>).</dd>
              <dt><code>{'|normalize'}</code></dt>
              <dd>Same as the legacy <code>_normalize</code> suffix.</dd>
            </dl>
          </section>

          <section>
            <h4>Lookup tables</h4>
            <dl>
              <dt><code>{'{name|lookup:<table>}'}</code></dt>
              <dd>
                Resolve the current value through a table defined inline on this
                source or in <em>Settings → Lookup Tables</em>. Keys that aren't
                found pass through unchanged.
              </dd>
            </dl>
          </section>

          <section>
            <h4>Conditionals</h4>
            <p className="template-help-hint">
              Content inside <code>{'{if:...}...{/if}'}</code> renders only when the condition is true.
              Conditionals may nest.
            </p>
            <dl>
              <dt><code>{'{if:group}...{/if}'}</code></dt>
              <dd>True when the group has a non-empty value.</dd>
              <dt><code>{'{if:group=value}...{/if}'}</code></dt>
              <dd>Exact string equality.</dd>
              <dt><code>{'{if:group~regex}...{/if}'}</code></dt>
              <dd>Regex match on the group's value. An invalid regex evaluates to false.</dd>
            </dl>
          </section>

          <section>
            <h4>Example</h4>
            <pre className="template-help-example">
              {'{league|uppercase}: {if:team}{team|titlecase}{/if}'}
            </pre>
            <p className="template-help-hint">
              With groups <code>league=nfl, team=chiefs</code> this renders as <code>NFL: Chiefs</code>.
              With <code>team</code> missing it renders as <code>NFL: </code>.
            </p>
          </section>
        </div>
      )}
    </div>
  );
});
