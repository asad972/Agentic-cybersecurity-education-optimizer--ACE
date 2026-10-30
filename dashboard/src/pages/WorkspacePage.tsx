import { ArrowRight, Construction, Layers } from 'lucide-react';

type Props = { title: string; description: string; owner: string; focus: string[] };

export function WorkspacePage({ title, description, owner, focus }: Props) {
  return <>
    <section className="page-heading">
      <span className="eyebrow">CYBERSECURITY OBSERVATORY</span>
      <h1>{title}</h1>
      <p>{description}</p>
    </section>
    <section className="workspace-card" aria-labelledby="workspace-title">
      <div className="workspace-art" aria-hidden="true"><Layers size={44} strokeWidth={1.3} /><span className="orbit orbit-one"/><span className="orbit orbit-two"/></div>
      <span className="status-label"><Construction size={15} /> Dashboard in preparation</span>
      <h2 id="workspace-title">A place for the bigger picture.</h2>
      <p className="workspace-copy">This workspace is ready for its dashboard. Data, charts and insights will appear here as the team completes this part of the observatory.</p>
      <div className="owner"><span className="owner-avatar">{owner.slice(0, 2)}</span><span><small>WORKSPACE OWNER</small><strong>{owner}</strong></span></div>
      <div className="planned"><span className="eyebrow">WHAT'S COMING</span><ul>{focus.map(item => <li key={item}><ArrowRight size={16} /><span>{item}</span></li>)}</ul></div>
    </section>
    <p className="data-note">No observations or live data are shown in this workspace preview.</p>
  </>;
}
