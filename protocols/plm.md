# PLM (Product Lifecycle Management) Protocol

## Stack
- Backend: Python (FastAPI) — engineering calculations, BOM management
- Frontend: TypeScript (React) — CAD viewer, BOM editor, workflow dashboard
- Database: PostgreSQL (relational) + file storage (S3/local) for CAD files
- Integration: ERP systems, CAD tools (STEP/IGES import)

## Engineering Rules
- All calculations must include: formula, inputs, units, result, tolerance
- Floating point: use Decimal type for financial, numpy for engineering
- Tolerance notation: bilateral (±0.01mm) or unilateral (+0.02/-0.00mm)
- Unit system: SI primary, imperial conversion layer
- BOM accuracy: part numbers, quantities, suppliers, lead times
- Revision control: every engineering change tracked with reason + approver

## BOM (Bill of Materials)
- Hierarchical: assembly → sub-assembly → part → raw material
- Fields: part_number, description, quantity, unit, supplier, cost, lead_time
- Where-used tracking: which assemblies contain a given part
- Mass rollup: calculate total weight from component weights
- Cost rollup: calculate total cost from component costs
- Export: CSV, Excel, PDF formats

## Tolerance & Precision
- Dimensions: 3 decimal places minimum (mm)
- Angles: 2 decimal places (degrees) or radians with 6 decimals
- Weights: grams (3 decimal) or kg (6 decimal)
- Currency: 2 decimal places, stored as integer cents internally
- Stack-up analysis: RSS (root sum of squares) method default

## Workflow Rules
- ECR (Engineering Change Request) → ECN (Engineering Change Notice) flow
- Multi-stage approval: engineer → lead → manager
- Impact analysis required before approval
- Effectivity dates on all changes
- Supersedure chain: old part → new part mapping

## CAD Integration
- STEP/IGES file import for 3D geometry
- Thumbnail generation for parts catalog
- Metadata extraction (dimensions, material, mass)
- Version sync: CAD file version linked to BOM revision

## Testing
- Unit: all engineering calculations with known results
- Tolerance: boundary value testing (min/nominal/max)
- BOM: rollup accuracy, circular reference detection
- Workflow: state machine transitions, approval chains
- Export: format validation, data integrity

## Filesystem Access
Workers, Kimi, and the Orchestrator have FULL filesystem access to the project folder.
This includes: creating/editing/deleting files, running Docker, executing scripts,
processing CAD files, generating reports, and any operational task.
NO human permission required for operational actions.
Human involvement ONLY for: engineering decisions, tolerance changes, and escalations.
