import { useState } from "react";
import { api } from "../api/client";
import type { Certificate, CertificateVerifyResponse } from "../api/types";
import { Badge, Button, ErrorBox, Spinner, downloadText } from "./ui";

/** Download a certificate and re-check it on the server (which rebuilds the automaton from the constraint text). */
export function CertificatePanel({ certificate }: { certificate: Certificate }) {
  const [result, setResult] = useState<CertificateVerifyResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function verify() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.verify(certificate));
    } catch (e) {
      setResult(null);
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted">
        A certificate records the automaton's run on the output (the state after every character) and a fingerprint of the automaton. Anyone can
        re-check it without the language model.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={verify} disabled={busy}>
          Verify certificate
        </Button>
        <Button onClick={() => downloadText("grammarguard-certificate.json", JSON.stringify(certificate, null, 2))}>Download JSON</Button>
        {busy && <Spinner label="Replaying run…" />}
        {result && (
          <>
            <Badge tone={result.valid ? "good" : "bad"}>{result.valid ? "certificate valid" : "certificate INVALID"}</Badge>
            {result.valid && <Badge tone={result.accepted ? "good" : "warn"}>{result.accepted ? "text is in the language" : "text is not in the language"}</Badge>}
          </>
        )}
      </div>
      {result && <p className="text-xs text-muted">{result.reason}</p>}
      <ErrorBox error={error} />
    </div>
  );
}
