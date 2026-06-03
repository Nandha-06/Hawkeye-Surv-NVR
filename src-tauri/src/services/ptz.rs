use base64::{Engine, engine::general_purpose::STANDARD};
use chrono::Utc;
use rand::RngCore;
use reqwest::Client;
use sha1::{Digest, Sha1};

#[derive(Clone)]
pub struct PtzService {
    client: Client,
}

#[derive(Debug, Clone)]
pub struct PtzCredentials {
    pub username: String,
    pub password: String,
}

impl PtzService {
    pub fn new() -> Self {
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .build()
            .expect("failed to build ptz client");
        Self { client }
    }

    fn endpoint(&self, camera_base_url: &str) -> String {
        let trimmed = camera_base_url.trim_end_matches('/');
        if trimmed.ends_with("/onvif/ptz") || trimmed.ends_with("/onvif/device_service") {
            trimmed.to_string()
        } else {
            format!("{}/onvif/ptz", trimmed)
        }
    }

    fn password_digest(password: &str, nonce_b64: &str, created: &str) -> String {
        let nonce = STANDARD.decode(nonce_b64).unwrap_or_default();
        let mut hasher = Sha1::new();
        hasher.update(&nonce);
        hasher.update(created.as_bytes());
        hasher.update(password.as_bytes());
        let digest = hasher.finalize();
        STANDARD.encode(digest)
    }

    fn build_soap_envelope(_action: &str, body: &str, creds: &PtzCredentials) -> String {
        let nonce_b64 = STANDARD.encode(rand_bytes(16));
        let created = Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
        let digest = Self::password_digest(&creds.password, &nonce_b64, &created);

        format!(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<env:Header>
<wsse:Security>
<wsse:UsernameToken>
<wsse:Username>{username}</wsse:Username>
<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce}</wsse:Nonce>
<wsu:Created>{created}</wsu:Created>
</wsse:UsernameToken>
</wsse:Security>
</env:Header>
<env:Body>
{body}
</env:Body>
</env:Envelope>"#,
            username = xml_escape(&creds.username),
            digest = digest,
            nonce = nonce_b64,
            created = created,
            body = body
        )
    }

    pub async fn continuous_move(
        &self,
        camera_base_url: &str,
        profile_token: &str,
        pan: f32,
        tilt: f32,
        zoom: f32,
        timeout_secs: u32,
        creds: &PtzCredentials,
    ) -> Result<(), String> {
        let body = format!(
            r#"<tptz:ContinuousMove>
<tptz:ProfileToken>{profile}</tptz:ProfileToken>
<tptz:Velocity>
<tt:PanTilt x="{pan}" y="{tilt}"/>
<tt:Zoom x="{zoom}"/>
</tptz:Velocity>
<tptz:Timeout>PT{timeout}S</tptz:Timeout>
</tptz:ContinuousMove>"#,
            profile = xml_escape(profile_token),
            pan = pan,
            tilt = tilt,
            zoom = zoom,
            timeout = timeout_secs
        );
        self.send_soap(camera_base_url, "ContinuousMove", &body, creds)
            .await
    }

    pub async fn stop(
        &self,
        camera_base_url: &str,
        profile_token: &str,
        creds: &PtzCredentials,
    ) -> Result<(), String> {
        let body = format!(
            r#"<tptz:Stop>
<tptz:ProfileToken>{profile}</tptz:ProfileToken>
<tptz:PanTilt>true</tptz:PanTilt>
<tptz:Zoom>true</tptz:Zoom>
</tptz:Stop>"#,
            profile = xml_escape(profile_token)
        );
        self.send_soap(camera_base_url, "Stop", &body, creds).await
    }

    pub async fn goto_preset(
        &self,
        camera_base_url: &str,
        profile_token: &str,
        preset_token: &str,
        creds: &PtzCredentials,
    ) -> Result<(), String> {
        let body = format!(
            r#"<tptz:GotoPreset>
<tptz:ProfileToken>{profile}</tptz:ProfileToken>
<tptz:PresetToken>{preset}</tptz:PresetToken>
</tptz:GotoPreset>"#,
            profile = xml_escape(profile_token),
            preset = xml_escape(preset_token)
        );
        self.send_soap(camera_base_url, "GotoPreset", &body, creds)
            .await
    }

    async fn send_soap(
        &self,
        camera_base_url: &str,
        action: &str,
        body: &str,
        creds: &PtzCredentials,
    ) -> Result<(), String> {
        let envelope = Self::build_soap_envelope(action, body, creds);
        let url = self.endpoint(camera_base_url);
        let soap_action = format!("http://www.onvif.org/ver20/ptz/wsdl/{}", action);

        let resp = self
            .client
            .post(&url)
            .header("Content-Type", "application/soap+xml; charset=utf-8")
            .header("SOAPAction", format!("\"{}\"", soap_action))
            .body(envelope)
            .send()
            .await
            .map_err(|e| format!("ptz {} request failed: {}", action, e))?;

        let status = resp.status();
        let text = resp
            .text()
            .await
            .map_err(|e| format!("ptz {} read body failed: {}", action, e))?;

        if !status.is_success() {
            return Err(format!(
                "ptz {} returned {}: {}",
                action, status, truncate(&text, 200)
            ));
        }
        if text.contains("Fault") && text.contains("env:Fault") {
            return Err(format!(
                "ptz {} returned SOAP fault: {}",
                action,
                truncate(&text, 300)
            ));
        }
        Ok(())
    }
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        format!("{}...", &s[..max])
    }
}

fn rand_bytes(n: usize) -> Vec<u8> {
    let mut buf = vec![0u8; n];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    buf
}
