import { useEffect, useState } from "react";
import { MessageCircle, Play } from "lucide-react";
import { post, Row } from "../api";
import { Card, Chip, PageHead, useData } from "../ui";

export default function Demo() {
  const [text, setText] = useState("สวัสดีค่ะ สนใจแพ็กเกจ Full Planning วันที่ 13 ก.พ. 2027 ประมาณ 300 คน ยังว่างไหมคะ");
  const [line, setLine] = useState<Row | null>(null);
  const [run, setRun] = useState<Row | null>(null);
  const [err, setErr] = useState({ line: "", run: "" });
  const [busy, setBusy] = useState("");
  const { data: h, reload } = useData<Row>("/api/data/health");

  useEffect(() => { // poll the run log while a run is in progress
    if (!run) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [run, reload]);

  const send = async () => {
    setBusy("line"); setErr({ ...err, line: "" });
    try { setLine(await post("/api/demo/line", { text })); } catch (e: any) { setErr({ ...err, line: e.message }); }
    setBusy("");
  };
  const start = async () => {
    setBusy("run"); setErr({ ...err, run: "" });
    try { setRun(await post("/api/demo/run", {})); } catch (e: any) { setErr({ ...err, run: e.message }); }
    setBusy("");
  };
  const last = h?.runs?.[0];
  return (
    <>
      <PageHead title="Demo panel" sub="For the live demonstration: a couple writes on LINE, then the nightly pipeline runs on demand." />
      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="Send a LINE message as a new couple" question="A signed webhook, exactly as LINE sends it, to the chat gateway on port 8001.">
          <textarea className="input w-full h-28" value={text} onChange={(e) => setText(e.target.value)} maxLength={500} aria-label="Message text" />
          <button className="btn-primary mt-3" onClick={send} disabled={busy === "line" || !text.trim()}><MessageCircle size={15} aria-hidden />Send</button>
          {err.line && <p className="text-over text-[13px] mt-3">{err.line}</p>}
          {line && (
            <div className="mt-4 rounded-lg bg-page border border-line p-3 text-[13px]">
              <p className="font-medium">Accepted by the gateway at {new Date(line.received_at).toLocaleTimeString("en-GB")}</p>
              <p className="text-ink-2 mt-1">Landed in: <span className="font-mono text-[12px] break-all">{line.landed_file ?? "not found in the expected hourly file"}</span></p>
              <p className="text-ink-3 mt-1 text-[12px]">Inquiry {line.inquiry_id} created in the CRM. The next pipeline run moves the message to Bronze, then Silver.</p>
            </div>
          )}
        </Card>
        <Card title="Run the pipeline now" question="Triggers the Airflow DAG aisleco_daily, or the command line if Airflow is not running.">
          <button className="btn-primary" onClick={start} disabled={busy === "run" || h?.demo_running}><Play size={15} aria-hidden />Run now</button>
          {err.run && <p className="text-over text-[13px] mt-3">{err.run}</p>}
          {run && <p className="text-[13px] text-ink-2 mt-3">Started via {run.via === "airflow" ? `Airflow (${run.dag_run_id})` : `the command line (log ${run.log})`}.</p>}
          {last && (
            <div className="mt-4">
              <div className="flex items-center gap-2 text-[13px]">
                <span className="font-medium">Run #{last.run_id}</span>
                <Chip s={last.status === "success" ? "OK" : last.status === "failed" ? "Over" : "Tight"}>{last.status}</Chip>
                <span className="text-ink-3">as of {last.asof_date}</span>
              </div>
              <ol className="mt-3 grid gap-1 text-[13px]">
                {(h!.steps as Row[]).map((s) => (
                  <li key={`${s.step}${s.attempt}`} className="flex items-center justify-between gap-2 rounded-md bg-page px-3 py-1.5">
                    <span>{s.step}</span>
                    <span className="flex items-center gap-2 num text-ink-2">{s.duration_sec !== null ? `${Number(s.duration_sec).toFixed(1)} s` : ""}
                      <Chip s={s.status === "success" ? "OK" : s.status === "failed" ? "Over" : "Tight"}>{s.status}</Chip></span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
