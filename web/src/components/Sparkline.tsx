import { useQuery } from "@tanstack/react-query";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { api } from "../lib/api";

export function Sparkline({ checkId }: { checkId: string }) {
  const q = useQuery({ queryKey: ["history", checkId, 30], queryFn: () => api.history(checkId, 30) });
  const data = (q.data ?? []).filter((p) => p.observed !== null);
  if (data.length < 2) return <span className="muted">—</span>;
  const last = q.data?.at(-1);
  return (
    <div style={{ width: 120, height: 28 }} aria-label={`trend over ${data.length} runs`}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 3, bottom: 3, left: 0, right: 0 }}>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Line
            dataKey="observed"
            dot={false}
            strokeWidth={1.75}
            isAnimationActive={false}
            stroke={last?.status === "pass" ? "var(--good)" : "var(--bad)"}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
