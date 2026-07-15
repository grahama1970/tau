import { Check, RotateCcw, ShieldCheck } from "lucide-react";
import type { TransactionProjection } from "../types";

export function TransactionAttempts({ transaction }: { transaction: TransactionProjection }) {
  return <section className="transaction-attempts" data-qid="dag:transaction:attempts">
    <header><RotateCcw aria-hidden="true" size={15} /><strong>Bounded transaction</strong><span>{transaction.current_attempt}/{transaction.max_attempts}</span></header>
    <div className="transaction-attempts__flow">
      {transaction.attempts.map((attempt) => <article key={attempt.attempt} data-qid={`dag:transaction:attempt:${attempt.attempt}`}>
        <strong>Attempt {attempt.attempt}</strong>
        <span>Creator {attempt.producer_state ?? "pending"}</span>
        <span>Validator {attempt.validator_status ?? "pending"}</span>
        <span>Reviewer {attempt.reviewer_verdict ?? "pending"}</span>
        {attempt.reviewer_verdict === "REVISE" && <span className="revision"><RotateCcw size={12} />revision committed</span>}
        {attempt.reviewer_verdict === "PASS" && <span><Check size={12} />PASS claim</span>}
      </article>)}
      <div className={`transaction-acceptance transaction-acceptance--${transaction.state.toLowerCase()}`}>
        <ShieldCheck size={15} />Tau admission: {transaction.state}
      </div>
    </div>
  </section>;
}
