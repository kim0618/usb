"use client";

import { useState } from "react";
import {
  HCohortTable, HEvaluationPanel, HForwardOutcomes, HIssuerDetail, HSummary,
} from "@/components/h-forward";
import { StrategyTabs } from "@/components/section-tabs";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { strategiesApi, type HIssuerView } from "@/lib/strategies";

/** Strategy H's operating screen: the forward shadow cohort, beside A and E rather than inside them.
 *
 *  H holds no capital, so this screen shows states, thesis levels and forward observations where the
 *  A and E screens show equity and fills. Selecting an issuer loads its full detail (the D3/D4/D5/D6
 *  legs, its decision history and every horizon) from the issuer route. */
export default function StrategyHPage() {
  const state = useApi(strategiesApi.forward, 60_000);
  const [selected, setSelected] = useState<string | null>(null);

  const header = <PageHeader title="Strategy H · Forward Shadow"
    description="연구는 종결됐고 지금부터는 미래 데이터만 본다. WATCH와 REJECT는 포지션을 만들지 않으며, APPROVE 0은 정상 상태다."/>;
  if (state.loading) return <><StrategyTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><StrategyTabs/>{header}<ErrorState message={state.error || "H forward 조회 실패"} retry={state.refresh}/></>;

  const view = state.data;
  return <>
    <StrategyTabs/>
    {header}
    <HSummary view={view}/>
    <HEvaluationPanel evaluation={view.evaluation}/>
    <HCohortTable rows={view.rows}/>
    <HForwardOutcomes rows={view.rows} horizons={view.contract.horizons}/>
    <HIssuerPicker tickers={view.rows.map(row => row.ticker)} selected={selected} onSelect={setSelected}/>
    {/* keyed by ticker: useApi memoises its effect, so a new issuer needs a new mount */}
    {selected && <HIssuerPanel key={selected} ticker={selected}/>}
  </>;
}

function HIssuerPicker({ tickers, selected, onSelect }: {
  tickers: string[]; selected: string | null; onSelect: (ticker: string) => void;
}) {
  return <section aria-labelledby="h-pick-title" className="mb-5">
    <h2 id="h-pick-title" className="mb-2 font-semibold">종목 상세</h2>
    <div className="flex flex-wrap gap-2">
      {tickers.map(ticker => <button key={ticker} type="button" onClick={() => onSelect(ticker)}
        aria-pressed={selected === ticker}
        className={`inline-flex h-9 items-center rounded-lg border px-3.5 text-sm font-semibold transition-colors focus-visible:ring-2 focus-visible:ring-primary ${selected === ticker ? "border-primary bg-primary-soft text-primary" : "border-line bg-surface text-foreground-secondary hover:border-primary hover:bg-primary-soft hover:text-primary"}`}>
        {ticker}
      </button>)}
    </div>
  </section>;
}

/** One issuer's detail. Mounted per ticker so the fetch hook has one stable loader. */
function HIssuerPanel({ ticker }: { ticker: string }) {
  const detail = useApi<HIssuerView>(() => strategiesApi.forwardIssuer(ticker), 60_000);
  if (detail.loading) return <LoadingState/>;
  if (!detail.data || detail.data.available === false) {
    return <ErrorState message={detail.data?.reason || detail.error || `${ticker} 상세 조회 실패`} retry={detail.refresh}/>;
  }
  return <HIssuerDetail issuer={detail.data}/>;
}
