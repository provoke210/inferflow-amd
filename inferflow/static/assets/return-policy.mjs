export function checkReturn(input={}) {
  const allowed=['days_since_delivery','unused','packaging_intact'];
  if(!input||Array.isArray(input)||typeof input!=='object'||Object.keys(input).some(k=>!allowed.includes(k)))throw Error('Only delivery days, unused status and packaging condition are accepted.');
  const facts=Object.fromEntries(allowed.map(k=>[k,input[k]??null]));
  const d=facts.days_since_delivery;
  if(d!==null&&(!Number.isSafeInteger(d)||d<0))throw Error('Delivery days must be a non-negative whole number.');
  for(const k of ['unused','packaging_intact'])if(facts[k]!==null&&typeof facts[k]!=='boolean')throw Error('Condition values must be true, false or unknown.');
  const rows=[['days_since_delivery',d,d!==null&&d<=7,`Delivered ${d} days ago. Demo policy allows days 0 through 7 inclusive.`,'How many days ago was the item delivered?'],['unused',facts.unused,facts.unused===true,facts.unused===true?'The item is unused.':'The item has been used and does not meet the unused condition.','Is the item unused?'],['packaging_intact',facts.packaging_intact,facts.packaging_intact===true,facts.packaging_intact===true?'Packaging is intact. Unopened packaging is not an additional requirement.':'Packaging is not intact and does not meet the packaging condition.','Is the packaging intact?']];
  const missing_fields=[],unmet_conditions=[],questions=[],checks=[];
  for(const [field,value,met,explanation,question] of rows){if(value===null){missing_fields.push(field);questions.push(question);}else if(!met)unmet_conditions.push(field);checks.push({field,status:value===null?'unknown':met?'met':'not_met',explanation:value===null?'Not confirmed. '+question:explanation});}
  const status=missing_fields.length?'needs_information':unmet_conditions.length?'conditions_not_met':'ready_for_human_review';
  let explanation=status==='needs_information'?'More information is needed. Eligibility cannot be established.':status==='conditions_not_met'?'The supplied facts do not meet this demo shop policy. Refer to a person for verification or exceptions.':'The supplied facts meet the demo policy conditions and can go to human review. No return or refund is approved.';
  if(missing_fields.length&&unmet_conditions.length)explanation+=' Known facts also fail one or more conditions; see the checks below.';
  return {policy_version:'demo-return-v1',window_days:7,status,facts,checks,missing_fields,unmet_conditions,questions,explanation,human_review_required:true,automatic_action:false,source:'local_rules'};
}
