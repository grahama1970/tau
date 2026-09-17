// Iterate project-state -> research -> web strategy -> ticket synthesis -> ticket
// lifecycle until the repo is machine-ready to share or genuinely human-blocked.
// Controller properties in code (P1 evidence-chain digests refuse on mismatch,
// P2 lease fencing + guarded release + stale>24h actionable, P3 contamination
// guard, P4 final-audit veto, P5 post-round re-gate, P6 mid-round rebind, P7
// restart journal, P8 convergence, P9 CRIT split, P10 cap clamp).
// Config: <repo>/.pi/dev-cycle.json (max_iterations clamp 1..20 default 5, mode,
//         readiness_command, focus, journal_path default .pi/dev-cycle-journal.jsonl).
// Terminal states: ready_to_share | blocked_human | iteration_cap_reached | no_provider_capacity | audit_vetoed | stalled_no_progress.
// Diagram: dev-cycle.diagram.md ($create-architecture) — lanes, gates, terminal states.
// Launch: subagent({ workflowScriptPath: '<repo>/workflows/dev-cycle.workflow.js' (canonical; deployed copy
//          at /home/graham/.pi/agent/workflows/dev-cycle.workflow.js), cwd: <repo>, async: true, globalConcurrencyLimit: 1 })
const digestsSchema={type:'array',items:{type:'object',additionalProperties:false,properties:{path:{type:'string'},sha256:{type:'string'}},required:['path','sha256']}};
const recoverSchema={type:'object',additionalProperties:false,properties:{candidatePaths:{type:'array',items:{type:'string'}},proofSummary:{type:'string'},leaseToken:{type:'string'}},required:['candidatePaths','proofSummary','leaseToken']};
const reviewSchema={type:'object',additionalProperties:false,properties:{verdict:{type:'string',enum:['ready','changes_requested','external_block']},findings:{type:'array',items:{type:'string'}},scopedPaths:{type:'array',items:{type:'string'}},digests:digestsSchema,proofSummary:{type:'string'}},required:['verdict','findings','scopedPaths','digests','proofSummary']};
const publishSchema={type:'object',additionalProperties:false,properties:{published:{type:'boolean'},commit:{type:'string'},leaseTokenUsed:{type:'string'},verifiedDigests:digestsSchema,refusal:{type:'string'}},required:['published','commit','leaseTokenUsed','verifiedDigests']};
const postSchema={type:'object',additionalProperties:false,properties:{verdict:{type:'string',enum:['pass','blocked']},findings:{type:'array',items:{type:'string'}},proofSummary:{type:'string'},commit:{type:'string'},pathCheck:{type:'string',enum:['exact','mismatch']},mismatchedPaths:{type:'array',items:{type:'string'}},leaseTokenUsed:{type:'string'}},required:['verdict','findings','proofSummary','commit','pathCheck','mismatchedPaths','leaseTokenUsed']};
const closeSchema={type:'object',additionalProperties:false,properties:{closed:{type:'boolean'},leaseTokenUsed:{type:'string'}},required:['closed','leaseTokenUsed']};
const contaminationSchema={type:'object',additionalProperties:false,properties:{preserved:{type:'boolean'},contaminationPaths:{type:'array',items:{type:'string'}}},required:['preserved','contaminationPaths']};
const preflightSchema={type:'object',additionalProperties:false,properties:{availableApiModels:{type:'array',items:{type:'string'}},availableWebSeats:{type:'array',items:{type:'string'}},unavailable:{type:'array',items:{type:'string'}}},required:['availableApiModels','availableWebSeats','unavailable']};
const gate0Schema={type:'object',additionalProperties:false,properties:{repo:{type:'string'},maxIterations:{type:'integer'},mode:{type:'string',enum:['full','tickets-only']},readinessCommand:{type:'string'},focus:{type:'string'},availableApiModels:{type:'array',items:{type:'string'}},availableWebSeats:{type:'array',items:{type:'string'}},unavailable:{type:'array',items:{type:'string'}},journalPath:{type:'string'},journal:{type:'array',items:{type:'object',additionalProperties:false,properties:{repo:{type:'string'},issue:{type:'integer'},stage:{type:'string'},token:{type:'string'},commit:{type:'string'}},required:['repo','issue','stage','token']}}},required:['repo','maxIterations','mode','readinessCommand','focus','availableApiModels','availableWebSeats','unavailable','journalPath','journal']};
const gateSchema={type:'object',additionalProperties:false,properties:{phase:{type:'string',enum:['ready','blocked_human','continue']},detail:{type:'string'},humanActions:{type:'array',items:{type:'string'}},actionable:{type:'array',items:{type:'object',additionalProperties:false,properties:{issue:{type:'integer'},reason:{type:'string'}},required:['issue','reason']}},blockerFingerprints:{type:'array',items:{type:'string'}},dirtySnapshot:{type:'array',items:{type:'string'}},staleLeases:{type:'array',items:{type:'object',additionalProperties:false,properties:{issue:{type:'integer'},ageHours:{type:'number'},holder:{type:'string'},token:{type:'string'}},required:['issue','ageHours']}}},required:['phase','detail','humanActions','actionable','blockerFingerprints','dirtySnapshot','staleLeases']};
const prepSchema={type:'object',additionalProperties:false,properties:{newTickets:{type:'array',items:{type:'integer'}},strategyDigest:{type:'string'},notes:{type:'string'}},required:['newTickets','strategyDigest','notes']};
const auditSchema={type:'object',additionalProperties:false,properties:{openRemaining:{type:'array',items:{type:'object',additionalProperties:false,properties:{issue:{type:'integer'},reason:{type:'string'}},required:['issue','reason']}},originMain:{type:'string'},strandedCommitCount:{type:'integer'},actionableRemaining:{type:'array',items:{type:'object',additionalProperties:false,properties:{issue:{type:'integer'},reason:{type:'string'}},required:['issue','reason']}}},required:['openRemaining','originMain','strandedCommitCount','actionableRemaining']};
// P9 CRIT split: mutating children get the primary-checkout notes; qualification
// children (gates, reviews, post) get the disposable-clean-clone notes so no
// reviewer ever holds a publish-capable checkout.
const IMPL_NOTES='IMPL method notes: (1) work ONLY in the primary checkout of this repo: no worktrees, no branch switching, no stash or reset, preserve unrelated dirty bytes; (2) local HEAD may be intentionally stale because work lands by plumbing: compare bytes against origin/main via git show/ls-tree/diff origin/main and git merge-base --is-ancestor, never against local HEAD or bare git status; (3) untracked candidate files live in the working tree: check git status --porcelain before claiming a candidate does not exist; (4) chunk every command, each under timeout 300.';
const QUALIFY_NOTES='QUALIFY method notes: (1) qualify from a disposable pinned clean clone of origin/main created in a temp dir; clones NEVER publish, land, close or mutate anything: read-only qualification only; (2) never clone into the primary checkout; (3) chunk every command, each under timeout 300; (4) compare bytes against origin/main via git show/ls-tree/diff origin/main, never local HEAD.';
function pickWorker(models){
 return (models||[]).find(function(m){return m.indexOf('gpt-5.5')>=0;})||(models||[])[0]||undefined;
}
function pickReviewer(models,workerModel){
 const pool=(models||[]).filter(function(m){return m!==workerModel;});
 return pool.find(function(m){return m.indexOf('glm-5.3')>=0&&m.indexOf('flash')<0;})||pool[0]||workerModel||undefined;
}
function digestMap(list){
 const m={};(list||[]).forEach(function(d){m[d.path]=d.sha256;});return m;
}
function digestsEqual(a,b){
 const ma=digestMap(a),mb=digestMap(b),ka=Object.keys(ma),kb=Object.keys(mb);
 if(ka.length!==kb.length)return false;
 return ka.every(function(k){return ma[k]===mb[k];});
}
function fingerprintSet(list){
 return JSON.stringify((list||[]).slice().sort());
}
function leaseRelease(repo,issue,token){
 return 'bash /home/graham/workspace/experiments/agent-skills/skills/ticket/run.sh release '+issue+' --repo '+repo+' --reason <blocker-file> (guarded: only if this run still holds lease '+(token||'<unknown-token>')+')';
}
const flow={
async main(){
 const gate0=await runs.run('cycle-gate0',{agent:'scout',worktree:false,acceptance:false,output:false,timeoutMs:900000,toolBudget:{hard:30},outputSchema:gate0Schema,task:`Bootstrap gate. (1) Resolve the repo: git remote get-url origin in cwd -> owner/name; never query any other repository. (2) Read config <repo>/.pi/dev-cycle.json if present: max_iterations (clamp 1..20, default 5), mode (default full), readiness_command (default auto), focus (default empty), journal_path (default .pi/dev-cycle-journal.jsonl). If readiness_command is auto and repo is grahama1970/tau use: uv run tau developer-share status --json; otherwise readiness_command = none. (3) Run the shared provider preflight: bash /home/graham/.pi/agent/workflows/model-preflight.sh --timeout 90; report available/unavailable exactly. (4) RESTART JOURNAL: read the journal file at journal_path if it exists; parse each JSONL line as {repo,issue,stage,token,commit} and return the entries in journal[], plus journalPath. Read-only except the preflight ping itself.`});
 const repo=gate0.structuredOutput.repo;
 const cap=Math.min(Math.max(gate0.structuredOutput.maxIterations||5,1),20);
 const readinessCommand=gate0.structuredOutput.readinessCommand;
 const focus=gate0.structuredOutput.focus;
 const mode=gate0.structuredOutput.mode;
 const journal=gate0.structuredOutput.journal||[];
 const journalPath=gate0.structuredOutput.journalPath;
 let workerModel=pickWorker(gate0.structuredOutput.availableApiModels);
 let reviewerModel=pickReviewer(gate0.structuredOutput.availableApiModels,workerModel);
 let webSeats=gate0.structuredOutput.availableWebSeats;
 const iterations=[];
 let finalState=null;
 if(!workerModel){
  finalState={status:'no_provider_capacity',detail:'preflight found no usable models'};
 }
 for(let i=1;i<=cap&&!finalState;i++){
  // P6 mid-round rebind: every iteration re-preflights cheaply and rebinds
  // roles from survivors; a dead bound model never launches a round.
  const pf=await runs.run('preflight-'+i,{agent:'scout',worktree:false,acceptance:false,output:false,timeoutMs:120000,toolBudget:{hard:6},outputSchema:preflightSchema,task:`Cheap provider re-preflight: bash /home/graham/.pi/agent/workflows/model-preflight.sh --timeout 30; report availableApiModels, availableWebSeats, unavailable exactly. Read-only except the ping.`});
  const survivors=pf.structuredOutput.availableApiModels||[];
  if(survivors.length===0){
   finalState={status:'no_provider_capacity',detail:'iteration '+i+' re-preflight found no provider capacity',iterations:iterations.length};
   break;
  }
  const rebinds=[];
  if(survivors.indexOf(workerModel)<0){const to=pickWorker(survivors);rebinds.push({role:'worker',from:workerModel,to:to});workerModel=to;}
  // Rebind the reviewer too when it died OR when the worker rebind collapsed
  // reviewer and worker onto the same survivor and another model exists.
  const reviewerGone=!reviewerModel||survivors.indexOf(reviewerModel)<0;
  const reviewerCollapsed=reviewerModel===workerModel&&survivors.length>1;
  if(reviewerGone||reviewerCollapsed){const to=pickReviewer(survivors,workerModel);if(to!==reviewerModel){rebinds.push({role:'reviewer',from:reviewerModel||null,to:to});reviewerModel=to;}}
  webSeats=pf.structuredOutput.availableWebSeats||[];
  const gate=await runs.run('gate-'+i,{agent:'scout',worktree:false,acceptance:false,output:false,timeoutMs:900000,toolBudget:{hard:35},outputSchema:gateSchema,task:`Iteration gate for ${repo}. (1) Readiness: ${readinessCommand!=='none'?`run \`${readinessCommand}\` from a CLEAN clone of current origin/main (temp dir). Phase=ready iff readiness is READY/ok or the only failing gates are human-acceptance/needs-human owned; include the gate list in detail.`:'no readiness command is configured; phase=ready only if there are no open actionable tickets AND no human/owned/dependency blockers remain.'} (2) Live issue classification via gh (this repo only): actionable = open, agent-routable, not maintainer-active, not needs-human/maintainer-blocked, declared dependencies CLOSED (verify cross-repo ones live). humanActions = the exact one-line action per remaining human/owned/dependency blocker; phase=blocked_human ONLY when that list is non-empty. (3) DIRTY SNAPSHOT: git status --porcelain in the primary checkout; return changed paths (up to 200) in dirtySnapshot as the contamination baseline. (4) STALE LEASES: report tickets holding a ticket-skill lease as staleLeases {issue,ageHours,holder,token}; a lease older than 24 hours must NOT make its issue non-actionable: classify it stale and leave the issue actionable-with-reason. (5) CONVERGENCE: return blockerFingerprints[] = one stable opaque token per distinct remaining blocker (issue number or normalized blocker-text hash); empty when phase==ready. Read-only. ${QUALIFY_NOTES}`});
  let phase=gate.structuredOutput.phase;
  let humanActions=gate.structuredOutput.humanActions||[];
  let gapSynthesis=false;
  // Missing blocker evidence is not blocked_human: an empty humanActions list
  // with phase=blocked_human means the gate failed to enumerate, so the round
  // continues (full mode re-synthesizes tickets) instead of declaring blocked.
  if(phase==='blocked_human'&&humanActions.length===0){phase='continue';gapSynthesis=true;}
  if(phase!=='continue'){
   finalState={status:phase==='ready'?'ready_to_share':'blocked_human',detail:gate.structuredOutput.detail,humanActions:humanActions,iterations:iterations.length+1};
   break;
  }
  // P8 convergence: identical blocker fingerprints two gates in a row means the
  // loop is not making progress; stop honestly instead of burning the cap.
  const fp=fingerprintSet(gate.structuredOutput.blockerFingerprints);
  if(iterations.length>0&&fp===iterations[iterations.length-1].blockerFingerprints){
   finalState={status:'stalled_no_progress',detail:'gate blocker fingerprints identical to the previous iteration',iterations:iterations.length+1};
   break;
  }
  const dirtySnapshot=gate.structuredOutput.dirtySnapshot||[];
  let targets=gate.structuredOutput.actionable.slice();
  // P2: stale leases (>24h) are actionable-with-reason regardless of how the
  // gate classified them; the controller merges them into the target queue.
  for(const s of (gate.structuredOutput.staleLeases||[])){
   if((s.ageHours||0)>24&&!targets.some(function(t){return t.issue===s.issue;})){
    targets.push({issue:s.issue,reason:'stale lease (>24h'+(s.holder?', held by '+s.holder:'')+') reclassified actionable-with-reason'});
   }
  }
  if(mode==='full'){
   const prep=await runs.run('prepare-'+i,{agent:'worker',model:workerModel,worktree:false,acceptance:false,output:false,timeoutMs:7200000,toolBudget:{hard:150},outputSchema:prepSchema,task:`Prepare the next ticket round for ${repo} with focus: ${focus||'top readiness gaps'}. Steps, loading each skill SKILL.md first: (1) PROJECT-STATE: /home/graham/workspace/experiments/agent-skills/skills/project-state/run.sh report --json --cached (cwd repo). (2) RESEARCH: $brave-search web, 2-4 varied queries from the top gaps + focus. (3) STRATEGY: ${webSeats.length>0?`compose ONE comprehensive-context question (condensed state + research + focus), run the MANDATORY browser preflight, then cd /home/graham/workspace/experiments/agent-skills/skills/ask && ./run.sh one-shot "<question>" --handler ${webSeats.join(' --handler ')} --json; report per-seat answers separately (no consensus by design).`:'NO web seats passed preflight: skip the one-shot, note the degradation, rely on state+research only.'} (4) SYNTHESIZE: distill everything into focused tickets via /home/graham/workspace/experiments/agent-skills/skills/ticket/run.sh fleet <file> — one independently verifiable acceptance criterion, concrete target, deterministic proof, route per item; review the dry-run previews for contract compliance; then --apply. Do not file tickets for work already covered by open issues. Return new ticket numbers and the strategy digest. ${IMPL_NOTES}`});
   for(const n of prep.structuredOutput.newTickets){targets.push({issue:n,reason:'filed this iteration'});}
   iterations.push({iteration:i,gapSynthesis:gapSynthesis,blockerFingerprints:fp,rebinds:rebinds,newTickets:prep.structuredOutput.newTickets,digest:prep.structuredOutput.strategyDigest.slice(0,400)});
  } else {
   iterations.push({iteration:i,gapSynthesis:gapSynthesis,blockerFingerprints:fp,rebinds:rebinds,newTickets:[],digest:'tickets-only mode'});
  }
  const roundResults=[];
  for(const t of targets){
   try{
    // P7 restart journal: resume at the first unmet stage; a ticket whose
    // publish or close already landed is never re-implemented or re-pushed.
    const stageRank={recover:1,publish:2,close:3};
    const entries=journal.filter(function(j){return j.repo===repo&&j.issue===t.issue;});
    let maxStage=0;
    for(const j of entries){if((stageRank[j.stage]||0)>maxStage)maxStage=stageRank[j.stage]||0;}
    if(maxStage>=3){
     roundResults.push({issue:t.issue,state:'already_closed',note:'journal shows close landed; never re-implement'});
     continue;
    }
    let expectedToken=null,publishedCommit=null,scope=[],digests=[];
    if(maxStage>=2){
     const pj=entries.filter(function(j){return j.stage==='publish';})[0]||{};
     expectedToken=pj.token||null;publishedCommit=pj.commit||null;
     if(!expectedToken||!publishedCommit){
      roundResults.push({issue:t.issue,state:'journal_incomplete',leaseAction:'release',leaseToken:expectedToken,releaseCommand:leaseRelease(repo,t.issue,expectedToken)});
      continue;
     }
    }else{
     const candidate=await runs.run('recover-'+i+'-'+t.issue,{agent:'worker',model:workerModel,worktree:false,acceptance:false,output:false,timeoutMs:7200000,toolBudget:{hard:140},outputSchema:recoverSchema,task:`Resolve ${repo}#${t.issue} (${t.reason}). Read the live issue first. Lease via /home/graham/workspace/experiments/agent-skills/skills/ticket/run.sh lease ${t.issue} --repo ${repo} --agent codex-worker-${t.issue}; if a lease already exists from a previous attempt of this same run identity, adopt its token instead of re-leasing. Implement the MINIMUM contract-compliant scope in the primary checkout: focused tests plus a retained real-world $agentic-evals fixture (live, not mocked) with artifact readback; follow the ticket body named skills/context files. Append one restart-journal JSONL line to ${journalPath} (create parent dirs): {"repo":"${repo}","issue":${t.issue},"stage":"recover","token":"<lease token>","commit":""}. Do not land or close; return candidatePaths, proofSummary and leaseToken. ${IMPL_NOTES}`});
     expectedToken=candidate.structuredOutput.leaseToken;
     if(!expectedToken||!(candidate.structuredOutput.candidatePaths||[]).length){
      roundResults.push({issue:t.issue,state:'recover_failed',leaseAction:'release',leaseToken:expectedToken,releaseCommand:leaseRelease(repo,t.issue,expectedToken)});
      continue;
     }
     let review=await runs.run('review-'+i+'-'+t.issue+'-0',{agent:'general-purpose',model:reviewerModel,worktree:false,acceptance:false,output:false,timeoutMs:1200000,toolTimeoutMs:180000,toolBudget:{hard:55},outputSchema:reviewSchema,task:`Bounded independent candidate review for ${repo}#${t.issue}. Live acceptance clauses vs candidate diff vs origin/main (untracked included), focused tests run, retained eval READY, scope isolation. Pre-publication scoped diff EXPECTED. For EVERY candidatePath compute the exact sha256 of the current bytes and return it in digests; those digests become the publish contract. ${QUALIFY_NOTES} Return ready+scopedPaths+digests, exact findings, or external_block.`});
     for(let round=1;round<=2&&review.structuredOutput.verdict==='changes_requested';round++){
      const findings=JSON.stringify(review.structuredOutput.findings);
      await runs.run('repair-'+i+'-'+t.issue+'-'+round,{agent:'worker',model:workerModel,worktree:false,acceptance:false,output:false,timeoutMs:7200000,toolBudget:{hard:140},task:`Repair ${repo}#${t.issue} from findings ${findings}. Minimal fixes, refresh proof/eval, preserve unrelated work. No landing, no closing. ${IMPL_NOTES}`});
      review=await runs.run('review-'+i+'-'+t.issue+'-'+round,{agent:'general-purpose',model:reviewerModel,worktree:false,acceptance:false,output:false,timeoutMs:1200000,toolTimeoutMs:180000,toolBudget:{hard:55},outputSchema:reviewSchema,task:`Bounded re-review ${repo}#${t.issue} round ${round}; recompute per-path sha256 digests for the returned scopedPaths. ${QUALIFY_NOTES} ready, findings, or external blocker.`});
     }
     if(review.structuredOutput.verdict!=='ready'){
      roundResults.push({issue:t.issue,state:review.structuredOutput.verdict,leaseAction:'release',leaseToken:expectedToken,releaseCommand:leaseRelease(repo,t.issue,expectedToken)});
      continue;
     }
     scope=review.structuredOutput.scopedPaths||[];
     digests=review.structuredOutput.digests||[];
     // P3 contamination guard: the gate's dirty baseline must be preserved
     // outside the approved scope before anything is published.
     const contam=await runs.run('contamination-'+i+'-'+t.issue,{agent:'scout',worktree:false,acceptance:false,output:false,timeoutMs:300000,toolBudget:{hard:12},outputSchema:contaminationSchema,task:`Contamination check for ${repo}#${t.issue}. Baseline dirty paths (from the iteration gate): ${JSON.stringify(dirtySnapshot.slice(0,200))}. Approved scope (excluded from the check): ${JSON.stringify(scope)}. For every baseline path NOT in scope, verify the bytes are unchanged (git diff -- <path> empty and hash equality where tracked-adjacent). preserved=true iff no unrelated baseline path changed; else list the changed paths in contaminationPaths. Read-only. ${QUALIFY_NOTES}`});
     if(!contam.structuredOutput.preserved){
      roundResults.push({issue:t.issue,state:'contamination_detected',contaminationPaths:contam.structuredOutput.contaminationPaths||[],leaseAction:'release',leaseToken:expectedToken,releaseCommand:leaseRelease(repo,t.issue,expectedToken)});
      continue;
     }
     // P1+P2: publish verifies the approved digests and the lease token BEFORE
     // landing; the controller independently re-checks both and refuses to
     // proceed on any mismatch, even if the child claimed success.
     const pub=await runs.run('publish-'+i+'-'+t.issue,{agent:'worker',model:workerModel,worktree:false,acceptance:false,output:false,timeoutMs:3600000,toolBudget:{hard:90},outputSchema:publishSchema,task:`Publish approved ${repo}#${t.issue} paths ${JSON.stringify(scope)} under lease token ${expectedToken}. APPROVED DIGEST CONTRACT (verify each path's current bytes sha256 matches BEFORE any landing; refuse with published=false and refusal if any differ): ${JSON.stringify(digests)}. Re-run decisive focused gates, land ONLY explicit paths via /home/graham/workspace/experiments/agent-skills/skills/gh-land/run.sh, verify remote blob equality + ancestry, attach the deterministic proof comment. Return verifiedDigests (the digests you actually measured), leaseTokenUsed, commit. Append one restart-journal JSONL line to ${journalPath}: {"repo":"${repo}","issue":${t.issue},"stage":"publish","token":"${expectedToken}","commit":"<landed sha>"}. Do not close. ${IMPL_NOTES}`});
     const pv=pub.structuredOutput;
     const tokenOk=pv.leaseTokenUsed===expectedToken;
     const digestOk=digestsEqual(digests,pv.verifiedDigests);
     if(!tokenOk||!digestOk||!pv.published){
      const state=!tokenOk?'lease_mismatch':(!digestOk?'evidence_mismatch':'publish_refused');
      roundResults.push({issue:t.issue,state:state,refusal:pv.refusal||null,commit:pv.commit||null,leaseAction:'release',leaseToken:expectedToken,releaseCommand:leaseRelease(repo,t.issue,expectedToken)});
      continue;
     }
     publishedCommit=pv.commit;
    }
    const post=await runs.run('post-'+i+'-'+t.issue,{agent:'general-purpose',model:reviewerModel,worktree:false,acceptance:false,output:false,timeoutMs:1200000,toolTimeoutMs:180000,toolBudget:{hard:45},outputSchema:postSchema,task:`Bounded independent post-publication review ${repo}#${t.issue} from a fresh clean clone of the new tip. Published commit: ${publishedCommit}. Approved lease token: ${expectedToken}. Verify the commit touches EXACTLY the approved scope for this ticket (no unrelated paths, nothing missing): pathCheck='exact' only then, else 'mismatch' with mismatchedPaths. Gates pass, proof comment exists. ${QUALIFY_NOTES} pass only if closure safe.`});
    const ps=post.structuredOutput;
    if(ps.verdict!=='pass'||ps.pathCheck!=='exact'||ps.commit!==publishedCommit){
     roundResults.push({issue:t.issue,state:'post_blocked',findings:ps.findings||[],pathCheck:ps.pathCheck,leaseAction:'release',leaseToken:expectedToken,releaseCommand:leaseRelease(repo,t.issue,expectedToken)});
     continue;
    }
    const close=await runs.run('close-'+i+'-'+t.issue,{agent:'worker',model:workerModel,worktree:false,acceptance:false,output:false,timeoutMs:1800000,toolBudget:{hard:60},outputSchema:closeSchema,task:`Close ${repo}#${t.issue} via /home/graham/workspace/experiments/agent-skills/skills/ticket/run.sh close ${t.issue} --repo ${repo} --reason completed --proof <file> --results <agent_skills.ticket_closure_evidence.v1 with passing unit AND live e2e>. LEASE FENCE: verify the live lease token equals ${expectedToken} first; refuse (closed=false) on any mismatch. Verify live CLOSED. Never bypass the worktree audit; never raw-mutate labels. After the close is verified, append one restart-journal JSONL line to ${journalPath}: {"repo":"${repo}","issue":${t.issue},"stage":"close","token":"${expectedToken}","commit":"${publishedCommit}"}. Return closed and leaseTokenUsed. ${IMPL_NOTES}`});
    const cv=close.structuredOutput;
    if(cv.closed===true&&cv.leaseTokenUsed===expectedToken){
     roundResults.push({issue:t.issue,state:'closed',commit:publishedCommit});
    }else{
     roundResults.push({issue:t.issue,state:'close_refused',leaseTokenUsed:cv.leaseTokenUsed||null,leaseAction:'release',leaseToken:expectedToken,releaseCommand:leaseRelease(repo,t.issue,expectedToken)});
    }
   }catch(e){roundResults.push({issue:t.issue,state:'infrastructure_failure',error:String(e)});}
  }
  iterations[iterations.length-1].tickets=roundResults;
 }
 // P5 post-round re-gate: the cap bounds work rounds, not observation. When the
 // loop ends by cap, one final gate decides the honest terminal state.
 if(!finalState){
  const gf=await runs.run('gate-final',{agent:'scout',worktree:false,acceptance:false,output:false,timeoutMs:900000,toolBudget:{hard:35},outputSchema:gateSchema,task:`Post-round final gate for ${repo} (the iteration cap was reached; this observation round re-checks readiness before reporting). ${readinessCommand!=='none'?`Run \`${readinessCommand}\` from a CLEAN clone of current origin/main (temp dir). Phase=ready iff readiness is READY/ok or the only failing gates are human-acceptance/needs-human owned; include the gate list in detail.`:'no readiness command is configured; phase=ready only if there are no open actionable tickets AND no human/owned/dependency blockers remain.'} Classify remaining issues exactly as the iteration gates do (actionable / humanActions / staleLeases / dirtySnapshot / blockerFingerprints). phase=blocked_human ONLY with a non-empty humanActions list. Read-only. ${QUALIFY_NOTES}`});
  if(gf.structuredOutput.phase==='ready'){
   finalState={status:'ready_to_share',detail:gf.structuredOutput.detail,humanActions:gf.structuredOutput.humanActions||[],iterations:cap};
  }else if(gf.structuredOutput.phase==='blocked_human'&&(gf.structuredOutput.humanActions||[]).length>0){
   finalState={status:'blocked_human',detail:gf.structuredOutput.detail,humanActions:gf.structuredOutput.humanActions||[],iterations:cap};
  }else{
   finalState={status:'iteration_cap_reached',detail:'cap reached; post-round re-gate still reports work remaining',iterations:cap};
  }
 }
 // P4 final-audit veto: a contradictory audit downgrades any success state to
 // audit_vetoed; success can never outrun the evidence.
 const audit=await runs.run('final-audit',{agent:'scout',worktree:false,acceptance:false,output:false,timeoutMs:600000,toolBudget:{hard:25},outputSchema:auditSchema,task:`Final bounded audit of ${repo} ONLY: classify every remaining open issue by exact reason (openRemaining) and separately list the ones that are agent-actionable right now (actionableRemaining); verify origin/main and that origin/main..HEAD is empty (strandedCommitCount). Read-only.`});
 const a=audit.structuredOutput;
 let status=finalState.status;
 let vetoReason=null;
 if((a.strandedCommitCount||0)>0){
  vetoReason='audit found '+a.strandedCommitCount+' stranded commit(s): origin/main..HEAD is not empty';
 }else if(status==='ready_to_share'&&(a.actionableRemaining||[]).length>0){
  vetoReason='audit found '+a.actionableRemaining.length+' remaining actionable issue(s) contradicting the ready state';
 }
 if(vetoReason)status='audit_vetoed';
 const unreleasedLeases=[];
 for(const it of iterations){
  for(const r of (it.tickets||[])){
   if(r.leaseAction==='release')unreleasedLeases.push({issue:r.issue,token:r.leaseToken||null,releaseCommand:r.releaseCommand});
  }
 }
 return {repo:repo,status:status,detail:finalState.detail,vetoReason:vetoReason,humanActions:finalState.humanActions||[],modelsUsed:{worker:workerModel,reviewer:reviewerModel,webSeats:webSeats},unavailableProviders:gate0.structuredOutput.unavailable,iterations:iterations,unreleasedLeases:unreleasedLeases,finalAudit:a};
}
};
return flow.main();
