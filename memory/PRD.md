# AurorAI — Intelligent Document Processing (IDP) PRD

## Mission
AurorAI transforme des documents “bloqués” (PDF, scans, formulaires, contrats, factures) en **données structurées, recherchables et exploitables**, avec **traçabilité**, **sécurité** et **conformité** pour les environnements réglementés.

## Positionnement
- Passage de fichiers statiques vers information vivante (indexable, analysable, routable, auditable).
- Réduction des erreurs opérationnelles et des risques de conformité.
- Priorité aux secteurs à volume élevé et exigences de contrôle strictes: finance, santé, public, juridique, éducation.

## Pipeline canonique IDP
1. Ingestion : upload / boîte mail / dépôt / API
2. Lecture (OCR) : texte depuis scans, images, PDF
3. Compréhension (NLP) : contexte, entités, relations
4. Classification : catégorie + document_type
5. Extraction : champs clés vers schéma JSON/CSV + score de confiance
6. Contrôle (HITL) : validation humaine ciblée en cas de faible confiance
7. Routage & intégration : ERP/CRM/DMS, workflows, alertes
8. Gouvernance : journal d’audit, versioning, rétention, contrôle d’accès

## Comportement opérationnel (golden rules)
- Toujours produire une sortie lisible par humain + une sortie machine (JSON).
- Toujours inclure : type + catégorie, champs extraits, confidence globale/par champ, ambiguïtés/manquants/anomalies, prochaines actions.
- Ton d’exécution : rapide, précis, orienté risque.

## État d’implémentation (actuel)
- [x] Upload documentaire (PDF, DOC, DOCX, TXT)
- [x] Catégorisation IA
- [x] Résumé IA
- [x] Extraction de citations
- [x] Bibliothèque documentaire + filtres
- [x] Reading lists

## Upgrades IDP intégrés
### P0 (indispensable)
- [x] Prompt de classification IDP avec sortie JSON stricte (category, document_type, confidence, rationale)
- [x] Prompt de résumé opérationnel (purpose, key points, obligations/deadlines, risques)
- [x] Extraction de champs structurés (`/documents/{id}/extract`) avec confidence/evidence
- [x] Contrôles automatiques (manquants, incohérences, doublons, formats invalides)
- [x] Signal `review_required` pour Human-in-the-loop
- [x] Journal d’audit des actions IA (classification, summary, extraction)

### P1 (réglementé + entreprise)
- [ ] Schémas dédiés par document_type (invoice, claim, contract, intake form, HR file)
- [ ] RBAC fin + masquage PII
- [ ] Versioning complet + politiques de rétention paramétrables

### P2 (scalabilité)
- [ ] Traitement asynchrone par queue
- [ ] Traitement par lots avancé
- [ ] Monitoring latence / taux d’erreur / qualité extraction

## API IDP (backend)
- `GET /api/` : message + mission + pipeline IDP
- `GET /api/idp/pipeline` : pipeline et règles d’or
- `POST /api/documents/{id}/categorize` : classification IDP enrichie
- `POST /api/documents/{id}/summary` : résumé opérationnel
- `POST /api/documents/{id}/extract` : extraction structurée + contrôles + conformité + actions
- `POST /api/documents/{id}/citations` : citations

## Message marché
- AurorAI automatise la lecture, la classification et l’extraction de données depuis vos documents.
- Elle accélère les workflows, améliore l’exactitude et renforce la conformité via audit, contrôle et validation ciblée.
