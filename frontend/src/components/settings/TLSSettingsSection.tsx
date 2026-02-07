/**
 * TLSSettingsSection Component
 *
 * Admin panel for configuring TLS/SSL certificates with Let's Encrypt
 * or manual certificate upload.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '../../services/api';
import type { TLSStatus, TLSSettings } from '../../types';
import { useNotifications } from '../../contexts/NotificationContext';
import './TLSSettingsSection.css';

interface Props {
  isAdmin: boolean;
}

export function TLSSettingsSection({ isAdmin }: Props) {
  const notifications = useNotifications();
  const [status, setStatus] = useState<TLSStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [dnsChallenge, setDnsChallenge] = useState<string | null>(null);

  // Form state
  const [enabled, setEnabled] = useState(false);
  const [mode, setMode] = useState<'letsencrypt' | 'manual'>('letsencrypt');
  const [domain, setDomain] = useState('');
  const [httpsPort, setHttpsPort] = useState(6143);
  const [acmeEmail, setAcmeEmail] = useState('');
  const [useStaging, setUseStaging] = useState(false);
  const [dnsProvider, setDnsProvider] = useState('');
  const [dnsApiToken, setDnsApiToken] = useState('');
  const [dnsZoneId, setDnsZoneId] = useState('');
  // AWS Route53 credentials
  const [awsAccessKeyId, setAwsAccessKeyId] = useState('');
  const [awsSecretAccessKey, setAwsSecretAccessKey] = useState('');
  const [awsRegion, setAwsRegion] = useState('us-east-1');
  const [autoRenew, setAutoRenew] = useState(true);
  const [renewDaysBefore, setRenewDaysBefore] = useState(30);

  // File upload refs
  const certFileRef = useRef<HTMLInputElement>(null);
  const keyFileRef = useRef<HTMLInputElement>(null);
  const chainFileRef = useRef<HTMLInputElement>(null);

  // Load status on mount
  useEffect(() => {
    if (!isAdmin) return;

    const loadData = async () => {
      try {
        setLoading(true);
        const [statusData, settingsData] = await Promise.all([
          api.getTLSStatus(),
          api.getTLSSettings(),
        ]);

        setStatus(statusData);

        // Populate form
        setEnabled(settingsData.enabled);
        setMode(settingsData.mode);
        setDomain(settingsData.domain);
        setHttpsPort(settingsData.https_port || 6143);
        setAcmeEmail(settingsData.acme_email);
        setUseStaging(settingsData.use_staging);
        setDnsProvider(settingsData.dns_provider);
        setDnsZoneId(settingsData.dns_zone_id);
        // AWS Route53 credentials (may be masked)
        if (settingsData.aws_access_key_id) setAwsAccessKeyId(settingsData.aws_access_key_id);
        if (settingsData.aws_secret_access_key) setAwsSecretAccessKey(settingsData.aws_secret_access_key);
        if (settingsData.aws_region) setAwsRegion(settingsData.aws_region);
        setAutoRenew(settingsData.auto_renew);
        setRenewDaysBefore(settingsData.renew_days_before_expiry);
      } catch (err) {
        notifications.error('Failed to load TLS settings', 'TLS');
        console.error('Failed to load TLS settings:', err);
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [isAdmin]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setDnsChallenge(null);

    try {
      await api.configureTLS({
        enabled,
        mode,
        domain,
        https_port: httpsPort,
        acme_email: acmeEmail,
        use_staging: useStaging,
        dns_provider: dnsProvider,
        dns_api_token: dnsApiToken,
        dns_zone_id: dnsZoneId,
        aws_access_key_id: awsAccessKeyId,
        aws_secret_access_key: awsSecretAccessKey,
        aws_region: awsRegion,
        auto_renew: autoRenew,
        renew_days_before_expiry: renewDaysBefore,
      });

      notifications.success('TLS settings saved');
      // Clear sensitive fields
      setDnsApiToken('');
      setAwsAccessKeyId('');
      setAwsSecretAccessKey('');

      // Refresh status
      const newStatus = await api.getTLSStatus();
      setStatus(newStatus);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save settings';
      notifications.error(message);
    } finally {
      setSaving(false);
    }
  }, [
    enabled, mode, domain, httpsPort, acmeEmail, useStaging,
    dnsProvider, dnsApiToken, dnsZoneId, awsAccessKeyId, awsSecretAccessKey, awsRegion,
    autoRenew, renewDaysBefore, notifications,
  ]);

  const handleRequestCertificate = useCallback(async () => {
    setRequesting(true);
    setDnsChallenge(null);

    try {
      const result = await api.requestCertificate();

      if (result.success) {
        notifications.success(result.message);
        // Refresh status
        const newStatus = await api.getTLSStatus();
        setStatus(newStatus);
      } else {
        if (result.txt_record_name) {
          // Show DNS challenge info inline (needs to persist on screen)
          setDnsChallenge(
            `DNS-01 Challenge Required:\n` +
            `Create a TXT record:\n` +
            `Name: ${result.txt_record_name}\n` +
            `Value: ${result.txt_record_value}\n\n` +
            `After creating the record, click "Complete Challenge".`
          );
        } else {
          // Simple error - just toast, don't clutter the page
          notifications.error(result.message);
        }
      }
    } catch (err) {
      // API errors - just toast, don't clutter the page
      const message = err instanceof Error ? err.message : 'Certificate request failed';
      notifications.error(message);
    } finally {
      setRequesting(false);
    }
  }, [notifications]);

  const handleCompleteDNSChallenge = useCallback(async () => {
    setRequesting(true);
    setDnsChallenge(null);

    try {
      const result = await api.completeDNSChallenge();

      if (result.success) {
        notifications.success(result.message);
        setDnsChallenge(null);
        // Refresh status
        const newStatus = await api.getTLSStatus();
        setStatus(newStatus);
      } else {
        notifications.error(result.message);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Challenge completion failed';
      notifications.error(message);
    } finally {
      setRequesting(false);
    }
  }, [notifications]);

  const handleUploadCertificate = useCallback(async () => {
    const certFile = certFileRef.current?.files?.[0];
    const keyFile = keyFileRef.current?.files?.[0];
    const chainFile = chainFileRef.current?.files?.[0];

    if (!certFile || !keyFile) {
      notifications.error('Please select both certificate and key files');
      return;
    }

    setRequesting(true);
    setDnsChallenge(null);

    try {
      const result = await api.uploadCertificate(certFile, keyFile, chainFile);

      if (result.success) {
        notifications.success(result.message);
        // Clear file inputs
        if (certFileRef.current) certFileRef.current.value = '';
        if (keyFileRef.current) keyFileRef.current.value = '';
        if (chainFileRef.current) chainFileRef.current.value = '';
        // Refresh status
        const newStatus = await api.getTLSStatus();
        setStatus(newStatus);
      } else {
        notifications.error(result.message);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Certificate upload failed';
      notifications.error(message);
    } finally {
      setRequesting(false);
    }
  }, [notifications]);

  const handleRenewCertificate = useCallback(async () => {
    setRequesting(true);
    setDnsChallenge(null);

    try {
      const result = await api.renewCertificate();

      if (result.success) {
        notifications.success(result.message);
        // Refresh status
        const newStatus = await api.getTLSStatus();
        setStatus(newStatus);
      } else {
        notifications.error(result.message);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Certificate renewal failed';
      notifications.error(message);
    } finally {
      setRequesting(false);
    }
  }, [notifications]);

  const handleDeleteCertificate = useCallback(async () => {
    if (!confirm('Are you sure you want to delete the certificate and disable TLS?')) {
      return;
    }

    setRequesting(true);
    setDnsChallenge(null);

    try {
      const result = await api.deleteCertificate();
      notifications.success(result.message);
      // Refresh status
      const newStatus = await api.getTLSStatus();
      setStatus(newStatus);
      setEnabled(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete certificate';
      notifications.error(message);
    } finally {
      setRequesting(false);
    }
  }, [notifications]);

  const handleTestDNSProvider = useCallback(async () => {
    if (!dnsProvider) {
      notifications.error('Please select a DNS provider');
      return;
    }

    // Validate credentials based on provider
    if (dnsProvider === 'cloudflare' && !dnsApiToken) {
      notifications.error('Please enter Cloudflare API token');
      return;
    }
    if (dnsProvider === 'route53' && (!awsAccessKeyId || !awsSecretAccessKey)) {
      notifications.error('Please enter AWS Access Key ID and Secret Access Key');
      return;
    }

    try {
      const result = await api.testDNSProvider({
        provider: dnsProvider,
        api_token: dnsApiToken,
        zone_id: dnsZoneId,
        domain,
        aws_access_key_id: awsAccessKeyId,
        aws_secret_access_key: awsSecretAccessKey,
        aws_region: awsRegion,
      });

      if (result.success) {
        notifications.success(result.message);
        if (result.zone_id) {
          setDnsZoneId(result.zone_id);
        }
      } else {
        notifications.error(result.message);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'DNS provider test failed';
      notifications.error(message);
    }
  }, [dnsProvider, dnsApiToken, dnsZoneId, domain, awsAccessKeyId, awsSecretAccessKey, awsRegion, notifications]);

  if (!isAdmin) {
    return (
      <div className="tls-settings-section">
        <p className="tls-settings-no-access">Admin access required to view TLS settings.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="tls-settings-section">
        <div className="tls-settings-loading">Loading TLS settings...</div>
      </div>
    );
  }

  return (
    <div className="tls-settings-section">
      <div className="settings-page-header">
        <h2>TLS/SSL Certificate Management</h2>
        <p>Configure HTTPS with Let's Encrypt automatic certificates or manual certificate upload.</p>
      </div>

      {dnsChallenge && (
        <div className="tls-settings-error">
          <span className="material-icons">info</span>
          <pre>{dnsChallenge}</pre>
        </div>
      )}

      {/* Current Status */}
      {status && (
        <div className="tls-status-line">
          <span className="tls-status-label">Current Status:</span>
          <span className={`tls-status-badge ${status.enabled && status.has_certificate ? 'encrypted' : 'unencrypted'}`}>
            {status.enabled && status.has_certificate ? `Encrypted (port ${status.https_port})` : 'UNENCRYPTED'}
          </span>
          <span className="tls-status-fallback">HTTP fallback on port 6100</span>
        </div>
      )}

      {/* Configuration Form */}
      <div className="tls-config-card">
        <h3>
          <span className="material-icons">settings</span>
          Configuration
        </h3>

        <div className="tls-config-content">
          <div className="form-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span>Enable TLS/HTTPS</span>
            </label>
          </div>

          {enabled && (
          <>
            <div className="form-group">
              <label>Certificate Mode</label>
              <div className="radio-group">
                <label className="radio-option">
                  <input
                    type="radio"
                    name="mode"
                    value="letsencrypt"
                    checked={mode === 'letsencrypt'}
                    onChange={() => setMode('letsencrypt')}
                  />
                  <span>Let's Encrypt (Automatic)</span>
                </label>
                <label className="radio-option">
                  <input
                    type="radio"
                    name="mode"
                    value="manual"
                    checked={mode === 'manual'}
                    onChange={() => setMode('manual')}
                  />
                  <span>Manual Certificate Upload</span>
                </label>
              </div>
            </div>

            {mode === 'letsencrypt' && (
              <>
                <div className="form-group">
                  <label htmlFor="domain">Domain Name</label>
                  <input
                    type="text"
                    id="domain"
                    value={domain}
                    onChange={(e) => setDomain(e.target.value)}
                    placeholder="ecm.example.com"
                  />
                  <span className="form-hint">The domain where ECM will be accessible (must point to this server)</span>
                </div>

                <div className="form-group">
                  <label htmlFor="httpsPort">HTTPS Port</label>
                  <input
                    type="number"
                    id="httpsPort"
                    value={httpsPort}
                    onChange={(e) => setHttpsPort(parseInt(e.target.value) || 6143)}
                    min={1}
                    max={65535}
                  />
                  <span className="form-hint">HTTPS will listen on this port (default: 6143). HTTP always stays on 6100 as fallback.</span>
                </div>

                <div className="form-group">
                  <label htmlFor="acmeEmail">Email Address</label>
                  <input
                    type="email"
                    id="acmeEmail"
                    value={acmeEmail}
                    onChange={(e) => setAcmeEmail(e.target.value)}
                    placeholder="admin@example.com"
                  />
                  <span className="form-hint">Contact email for Let's Encrypt account (renewal notifications)</span>
                </div>

                <div className="form-group">
                  <label htmlFor="dnsProvider">DNS Provider (for automatic TXT record management)</label>
                  <select
                    id="dnsProvider"
                    value={dnsProvider}
                    onChange={(e) => setDnsProvider(e.target.value)}
                  >
                    <option value="">Manual / Other Provider</option>
                    <option value="cloudflare">Cloudflare (automatic)</option>
                    <option value="route53">AWS Route53 (automatic)</option>
                  </select>
                  <span className="form-hint">
                    Select Cloudflare or Route53 for automatic DNS record creation.
                    For other providers, select "Manual" and create the TXT record yourself when prompted.
                  </span>
                </div>

                {dnsProvider === 'cloudflare' && (
                  <div className="form-group">
                    <label htmlFor="dnsApiToken">Cloudflare API Token</label>
                    <input
                      type="password"
                      id="dnsApiToken"
                      value={dnsApiToken}
                      onChange={(e) => setDnsApiToken(e.target.value)}
                      placeholder="Enter Cloudflare API token..."
                    />
                    <span className="form-hint">API token with DNS:Edit permission for your zone</span>
                  </div>
                )}

                {dnsProvider === 'route53' && (
                  <>
                    <div className="form-group">
                      <label htmlFor="awsAccessKeyId">AWS Access Key ID</label>
                      <input
                        type="text"
                        id="awsAccessKeyId"
                        value={awsAccessKeyId}
                        onChange={(e) => setAwsAccessKeyId(e.target.value)}
                        placeholder="AKIA..."
                      />
                      <span className="form-hint">IAM user access key with Route53 permissions</span>
                    </div>

                    <div className="form-group">
                      <label htmlFor="awsSecretAccessKey">AWS Secret Access Key</label>
                      <input
                        type="password"
                        id="awsSecretAccessKey"
                        value={awsSecretAccessKey}
                        onChange={(e) => setAwsSecretAccessKey(e.target.value)}
                        placeholder="Enter secret access key..."
                      />
                    </div>

                    <div className="form-group">
                      <label htmlFor="awsRegion">AWS Region</label>
                      <select
                        id="awsRegion"
                        value={awsRegion}
                        onChange={(e) => setAwsRegion(e.target.value)}
                      >
                        <option value="us-east-1">US East (N. Virginia)</option>
                        <option value="us-east-2">US East (Ohio)</option>
                        <option value="us-west-1">US West (N. California)</option>
                        <option value="us-west-2">US West (Oregon)</option>
                        <option value="eu-west-1">EU (Ireland)</option>
                        <option value="eu-west-2">EU (London)</option>
                        <option value="eu-central-1">EU (Frankfurt)</option>
                        <option value="ap-northeast-1">Asia Pacific (Tokyo)</option>
                        <option value="ap-southeast-1">Asia Pacific (Singapore)</option>
                        <option value="ap-southeast-2">Asia Pacific (Sydney)</option>
                      </select>
                      <span className="form-hint">Route53 is global, but SDK requires a region</span>
                    </div>
                  </>
                )}

                <div className="form-group">
                  <label htmlFor="dnsZoneId">Zone/Hosted Zone ID (Optional)</label>
                  <input
                    type="text"
                    id="dnsZoneId"
                    value={dnsZoneId}
                    onChange={(e) => setDnsZoneId(e.target.value)}
                    placeholder="Auto-detected from domain"
                  />
                  <span className="form-hint">Leave empty to auto-detect from domain</span>
                </div>

                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleTestDNSProvider}
                  disabled={!dnsProvider}
                >
                  Test DNS Provider
                </button>

                <div className="form-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={useStaging}
                      onChange={(e) => setUseStaging(e.target.checked)}
                    />
                    <span>Use Staging Environment (for testing)</span>
                  </label>
                  <span className="form-hint">Uses Let's Encrypt staging server (certificates won't be trusted)</span>
                </div>

                <div className="form-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={autoRenew}
                      onChange={(e) => setAutoRenew(e.target.checked)}
                    />
                    <span>Auto-Renew Certificate</span>
                  </label>
                </div>

                {autoRenew && (
                  <div className="form-group">
                    <label htmlFor="renewDaysBefore">Renew Days Before Expiry</label>
                    <input
                      type="number"
                      id="renewDaysBefore"
                      value={renewDaysBefore}
                      onChange={(e) => setRenewDaysBefore(parseInt(e.target.value) || 30)}
                      min={1}
                      max={60}
                    />
                  </div>
                )}
              </>
            )}

            {mode === 'manual' && (
              <div className="manual-upload-section">
                <div className="form-group">
                  <label htmlFor="certFile">Certificate File (PEM)</label>
                  <input
                    type="file"
                    id="certFile"
                    ref={certFileRef}
                    accept=".pem,.crt,.cer"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="keyFile">Private Key File (PEM)</label>
                  <input
                    type="file"
                    id="keyFile"
                    ref={keyFileRef}
                    accept=".pem,.key"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="chainFile">Chain File (Optional)</label>
                  <input
                    type="file"
                    id="chainFile"
                    ref={chainFileRef}
                    accept=".pem,.crt"
                  />
                  <span className="form-hint">Intermediate certificates (if not included in certificate file)</span>
                </div>

                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleUploadCertificate}
                  disabled={requesting}
                >
                  {requesting ? 'Uploading...' : 'Upload Certificate'}
                </button>
              </div>
            )}
          </>
        )}
        </div>
      </div>

      {/* Actions */}
      <div className="tls-actions">
        <button
          type="button"
          className="btn-primary"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Saving...' : '1. Save Settings'}
        </button>

        {enabled && mode === 'letsencrypt' && (
          <>
            <button
              type="button"
              className="btn-secondary"
              onClick={handleRequestCertificate}
              disabled={requesting || !domain || !acmeEmail}
            >
              {requesting ? 'Requesting...' : '2. Request Certificate'}
            </button>

            {/* Only show Complete DNS Challenge for manual DNS setup (no provider configured) */}
            {!dnsProvider && (
              <button
                type="button"
                className="btn-secondary"
                onClick={handleCompleteDNSChallenge}
                disabled={requesting}
              >
                3. Complete DNS Challenge
              </button>
            )}
          </>
        )}

        {status?.has_certificate && (
          <>
            {status.mode === 'letsencrypt' && (
              <button
                type="button"
                className="btn-secondary"
                onClick={handleRenewCertificate}
                disabled={requesting}
              >
                {requesting ? 'Renewing...' : 'Renew Certificate'}
              </button>
            )}

            <button
              type="button"
              className="btn-danger"
              onClick={handleDeleteCertificate}
              disabled={requesting}
            >
              Delete Certificate
            </button>
          </>
        )}
      </div>

      {/* Info Box */}
      <div className="tls-info-box">
        <h4>
          <span className="material-icons">help_outline</span>
          About TLS Certificates
        </h4>
        <ul>
          <li>
            <strong>Dual-Port Setup:</strong> HTTP always runs on port 6100 as a fallback.
            HTTPS runs on the configured port (default 6143) when TLS is enabled.
          </li>
          <li>
            <strong>Let's Encrypt</strong> provides free, automated certificates valid for 90 days.
            Auto-renewal will request a new certificate before expiry.
          </li>
          <li>
            <strong>DNS-01 Challenge</strong> validates domain ownership via DNS TXT record.
            Requires API access to Cloudflare or AWS Route53. Works behind firewalls and NAT.
          </li>
          <li>
            <strong>Manual Upload</strong> allows using certificates from any Certificate Authority.
            You are responsible for renewal.
          </li>
          <li>
            After enabling TLS, ECM will restart. Access via HTTPS on port {httpsPort},
            or HTTP on port 6100 as fallback.
          </li>
        </ul>
      </div>
    </div>
  );
}

export default TLSSettingsSection;
