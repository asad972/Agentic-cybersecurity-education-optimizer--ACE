import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, BookOpen, ChevronRight, Menu, Shield, X } from 'lucide-react';
import { pages, pageFromHash } from './pages';

export default function App() {
  const [page, setPage] = useState(() => pageFromHash(window.location.hash));
  const [menuOpen, setMenuOpen] = useState(false);
  const main = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const changePage = () => {
      setPage(pageFromHash(window.location.hash));
      setMenuOpen(false);
      main.current?.focus();
    };
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
        menuButton.current?.focus();
      }
    };
    window.addEventListener('hashchange', changePage);
    window.addEventListener('keydown', closeWithEscape);
    return () => {
      window.removeEventListener('hashchange', changePage);
      window.removeEventListener('keydown', closeWithEscape);
    };
  }, []);

  useEffect(() => { document.title = page.name + ' · ACE'; }, [page]);
  const Page = page.component;

  return <div className="app">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); main.current?.focus(); }}>Skip to content</a>
    {menuOpen && <button className="nav-scrim" aria-label="Close navigation overlay" onClick={() => setMenuOpen(false)} />}
    <aside id="navigation" className={'sidebar' + (menuOpen ? ' is-open' : '')}>
      <a className="brand" href="#europe" aria-label="ACE home"><span className="brand-symbol"><Shield size={27}/></span><span>ACE<small>CYBERSECURITY OBSERVATORY</small></span></a>
      <button className="mobile-close icon-button" aria-label="Close navigation" onClick={() => { setMenuOpen(false); menuButton.current?.focus(); }}><X size={20}/></button>
      <span className="nav-label">EXPLORE THE OBSERVATORY</span>
      <nav aria-label="Main navigation">{pages.map(item => <a href={'#' + item.id} key={item.id} aria-current={page.id === item.id ? 'page' : undefined} onClick={() => setMenuOpen(false)}><item.icon size={19}/><span>{item.name}</span>{page.id === item.id && <ChevronRight size={15}/>}</a>)}</nav>
      <div className="sidebar-bottom"><div className="research-card"><BookOpen size={23}/><strong>Built for better decisions</strong><p>Connecting cybersecurity education, workforce intelligence and threat signals.</p></div><a className="project-link" href="https://github.com/asad972/Agentic-cybersecurity-education-optimizer--ACE" target="_blank" rel="noreferrer">Project & documentation<ArrowUpRight size={16}/></a><span className="sidebar-credit">AGENTIC CYBERSECURITY<br/>EDUCATION OPTIMIZER</span></div>
    </aside>
    <div className="content">
      <header className="topbar"><div className="breadcrumb"><button ref={menuButton} className="mobile-menu icon-button" aria-label="Open navigation" aria-expanded={menuOpen} aria-controls="navigation" onClick={() => setMenuOpen(!menuOpen)}><Menu size={21}/></button><span className="breadcrumb-root">Observatory</span><ChevronRight size={14}/><strong>{page.name}</strong></div><span className="preview-badge"><span/>Workspace preview</span></header>
      <main ref={main} id="main-content" tabIndex={-1}><Page/></main>
      <footer><span><Shield size={14}/>ACE · Education meets intelligence.</span><span>Team workspace · M4 shell release</span></footer>
    </div>
  </div>;
}
