# Build Backlog — KensaraAI SEO Agent
Last updated: 2026-06-08

## Priority 1 — Wire existing code (already built, just not connected)
- [ ] Wire quality scorer into blog pipeline — only drafts scoring 60+/100 reach queue
- [ ] Wire risk classifier — LOW risk auto-publishes, HIGH risk always hits CEO queue
- [ ] Wire job queue — failed generations retry 3x before alerting

## Priority 2 — CEO experience (high impact, low effort)
- [ ] Email notification — draft ready → CEO gets email with direct approve/reject link (no dashboard visit needed)
- [ ] WordPress auto-publish — CEO approves in dashboard → goes live on kensara.in (needs WP App Password from Harjinder)
- [ ] Cloud deploy (Fly.io) — permanent URL, no laptop dependency (needs flyctl auth login)
- [ ] Fix old GROQ_API_KEY in Windows environment variables (Harjinder to do manually)

## Priority 3 — Phase 2 LinkedIn
- [ ] LinkedIn post writer — 3 posts/week (fear, educational, social proof)
- [ ] LinkedIn API OAuth2 connection to KensaraAI company page
- [ ] LinkedIn publisher adapter
- Blocker: LinkedIn client ID + secret + access token from Harjinder

## Priority 4 — Phase 3 Newsletter
- [ ] Monthly "KensaraAI Privacy Digest" generator
- [ ] Pull stats from platform context (DSARs processed, consents recorded)
- [ ] Mailchimp send integration
- Blocker: Mailchimp API key from Harjinder

## Priority 5 — Analytics & Feedback loop
- [ ] Google Search Console OAuth2 setup (rank data from Google directly)
- [ ] Weekly performance report emailed to Harjinder (rankings + content generated + gaps)
- [ ] Enforcement tracker auto-update via Tavily (currently manual)

## Priority 6 — Authority content (manual effort, high SEO value)
- [ ] Build 54 cluster pages from topic_clusters.json (the real SEO moat)
- [ ] DPDPA vs GDPR deep comparison post (most searched informational query)
- [ ] DPDPA penalty calculator tool (interactive, linkable asset)
- [ ] Monthly enforcement tracker update (add new cases as they happen)

## Needs Harjinder to provide
- WordPress username + Application Password (kensara.in/wp-admin → Users → Profile → Application Passwords)
- LinkedIn app credentials (developers.linkedin.com)
- Mailchimp API key + list ID (mailchimp.com)
- Email address for notifications (worldofspecs@gmail.com already known)
