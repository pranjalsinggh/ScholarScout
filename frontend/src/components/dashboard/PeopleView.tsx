import { motion } from "framer-motion";
import { Building2, ExternalLink, Users } from "lucide-react";
import { GlassCard, Chip, SectionTitle, stagger } from "../glass/Glass";
import type { InstitutionItem, PersonProfile } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

interface PeopleData {
  original_authors: PersonProfile[];
  extenders: PersonProfile[];
  challengers: PersonProfile[];
  major_researchers: PersonProfile[];
  note?: string | null;
}

function PersonCard({ p, tone }: { p: PersonProfile; tone: string }) {
  return (
    <GlassCard className="glass-ring h-full p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 break-words">
          <div className="font-semibold text-white">{p.name}</div>
          {p.affiliation && <div className="text-xs text-faint">{p.affiliation}</div>}
        </div>
        <Chip tone={tone as "accent"}>{p.role}</Chip>
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-400">
        {p.h_index != null && <span>h-index {p.h_index}</span>}
        {p.works_count != null && <span>· {p.works_count} works</span>}
        {p.research_areas.slice(0, 2).map((a) => (
          <span key={a} className="truncate">· {a}</span>
        ))}
      </div>
      {p.notable_works.length > 0 && (
        <div className="mt-3 space-y-1.5 border-t border-white/8 pt-3">
          {p.notable_works.slice(0, 2).map((w, i) => (
            <a
              key={i}
              href={w.url || undefined}
              target="_blank"
              rel="noreferrer"
              className="block truncate text-xs text-slate-400 hover:text-azure-300"
            >
              ↳ {w.title}{w.year ? ` (${w.year})` : ""}
            </a>
          ))}
        </div>
      )}
      {p.profile_url && (
        <a href={p.profile_url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs text-azure-300 hover:text-azure-200">
          OpenAlex profile <ExternalLink size={11} />
        </a>
      )}
    </GlassCard>
  );
}

export default function PeopleView({ paperId }: { paperId: string }) {
  const profiles = useSectionResource<PeopleData>(`/papers/${paperId}/people`);
  const affiliations = useSectionResource<{ institutions: InstitutionItem[] }>(`/papers/${paperId}/institutions`);
  const people = profiles.data;
  const institutions = affiliations.data?.institutions;

  const groups: [string, PersonProfile[], string][] = [
    ["Original authors", people?.original_authors || [], "azure"],
    ["Extended the work", people?.extenders || [], "accent"],
    ["Challenged the findings", people?.challengers || [], "warn"],
    ["Major researchers in this area", people?.major_researchers || [], "default"],
  ];

  return (
    <div className="space-y-8">
      <div>
        <SectionTitle title="People behind the research" icon={<Users size={18} />} sub="Author names from the document and verified profiles from OpenAlex. Publication relationships are included when supported by research records." />
        {profiles.error ? <SectionState error title="People information could not load" onRetry={profiles.retry}>{profiles.error}</SectionState>
          : !people ? <SectionState loading title="Loading authors and research profiles…" />
          : <div className="space-y-6">
          {groups.map(([label, list, tone]) => (
              <div key={label}>
                <h3 className="mb-3 text-sm font-semibold text-slate-200">{label}</h3>
                {list.length > 0 ? <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {list.map((p, i) => (
                    <motion.div key={p.name} {...stagger(i * 0.04)}>
                      <PersonCard p={p} tone={tone} />
                    </motion.div>
                  ))}
                </div> : <SectionState title={label === "Original authors" ? "No document authors identified" : label === "Extended the work" ? "No verified extensions" : label === "Challenged the findings" ? "No verified challenges" : "No leading research profiles verified"}>
                  {label === "Original authors" ? "No author names or confidently matched author profiles are available. Named people in the body of a document are not assumed to be its authors."
                    : "This group requires a verified scholarly record and documented publication relationships. It may not apply to this document."}
                </SectionState>}
              </div>
          ))}
          {people.note && <p className="text-sm text-faint">{people.note}</p>}
        </div>}
      </div>

      <div>
        <SectionTitle title="Institution map" icon={<Building2 size={18} />} />
        {affiliations.error ? <SectionState error title="Institution information could not load" onRetry={affiliations.retry}>{affiliations.error}</SectionState> : !institutions ? (
          <SectionState loading title="Loading verified affiliations…" />
        ) : institutions.length === 0 ? (
          <SectionState title="No author affiliations identified">Institutions are included when tied to the authors or confirmed research records. Mentioning an institution in a document does not establish an affiliation.</SectionState>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {institutions.slice(0, 12).map((inst, i) => (
              <motion.div key={inst.name} {...stagger(i * 0.04)}>
                <GlassCard className="glass-ring p-4">
                  <div className="text-sm font-medium text-white">{inst.name}</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {inst.roles.map((r) => <Chip key={r} tone="azure">{r}</Chip>)}
                  </div>
                  <div className="mt-2 text-xs text-faint">{inst.related_papers} linked works</div>
                </GlassCard>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
