# PEAK Platform GraphQL Glossary

Reference for the MCP generic GraphQL tools (`describe_graphql_type`, `execute_graphql_query`). Read the conventions first — they prevent the most common field/arg errors — then the per-type reference.

---

## 1. Naming & traversal conventions

**Read before selecting fields.**

1. **Display-name field varies by type — there is no universal `name`:**
   Equipment → `name` · Metadata → `name` · MetadataType → `type` (+`type_code`) · Site → `site_name` · Level → `level_name` · Zone → `zone_name.zone_name` (the `zone_name` *field* is a ZoneName object; from an Equipment/Favourite that's `zone.zone_name.zone_name` — see Zone entry, two same-named hops) · Device → `bacnet_device_name` · DeviceObject → `object_name` · Favourite → none (`identifier`).

2. **A related entity's type/name/code is never a flat field on the instance — subselect the nested entity.** An equipment's type is `metadata_type { type, type_code }`, not `equipment_type_name` / `equipment_type_code` (neither exists). The FK (`metadata_type_id`) sits on the instance; the readable type lives one hop away on MetadataType. Same for points: `metadata { name, code }`, never `metadata_name` / `metadata_code`. *(This is the instance-vs-type split at the field level: wrong field AND wrong entity.)*

3. **No `site_id` on Equipment, Favourite, or Device.** Equipment/Favourite reach site via `zone.site_id` (one hop). **Device has no `zone`** — reach site via `collector.site_id` (or `collector.site`). Hierarchy: Site → Level → Zone → Equipment.

4. **`code` ≠ `type_code`.** Metadata's abbreviation is `code`; MetadataType's is `type_code`. Not interchangeable.

5. **All nested objects require subselection** (`zone`, `level`, `metadata`, `metadata_type`, `zone_name`, `device_object`, `primary_equipment`) — selecting them bare returns "Subselection required."

6. **Flat `*_name` / `*_code` strings are filter arguments, not selectable fields** — see §2.

---

## 2. Filtering: arguments vs. fields

The most common confusion after display-names: strings like `metadata_code`, `metadata_name`, `metadata_type_code` **exist in the schema as filter *arguments* on list sub-fields — not as selectable output fields.** The same string can be a valid arg here and an invalid field selection on the row type.

- **To read** a related value → subselect the nested object (`metadata { code }`).
- **To filter** a nested list → pass the arg to the sub-field.

**Mechanics (`execute_graphql_query`):** sub-field args go in the object form of a `fields` item — `{ "path": "...", "args": {...}, "sub_fields": [...] }` — never as a bare field name.

**Useful filterable sub-fields and their args** (`(s)` = both singular and plural variants exist):

| Sub-field | Filter args |
|---|---|
| `Site.equipment` | `name`, `metadata_type_id(s)`, `metadata_type_code(s)`, `model`, `vendor`, `is_active`, `shared`, `virtual` |
| `Site.favourites` | `metadata_id(s)`, `metadata_code(s)`, `metadata_name`, `metadata_type_code(s)`, `identifier`, `identifier_filter`, `is_active` |
| `Site.levels` / `Site.zones` | `system`, `has_ie_config`, `ie_excluded` |
| `Site.devices` | `bacnet_device_name`, `bacnet_device_id`, `device_label`, `model`, `vendor`, `virtual` |
| `Equipment.favourites` | `metadata_id(s)`, `metadata_code(s)`, `metadata_name`, `identifier`, `identifier_filter` |
| `Device.device_objects` | `metadata_id(s)`, `object_name`, `object_identifier(s)`, `zone_id(s)`, `equipment_id(s)`, `filter`, `limit`, `start_index` |
| `Device.primary_equipment` | `name`, `metadata_type_id(s)`, `metadata_type_code(s)`, `model`, `vendor`, `shared`, `virtual` |
| `Favourite.history` | `start`, `end`, `ts`, `latest`, `end_exclusive` |

**Caveats:**

- **Sub-field args ≠ top-level query args.** `limit` / `start_index` are generally **not** available on the entity sub-lists (`equipment`, `favourites`, `zones`, `levels` — confirmed none take them); exceptions are `Device.device_objects` and the time-series sub-lists (`Site.snooze_monitoring`, `Site.nabers_bounds_schedules`, `Site.site_targets`). Don't assume pagination on a sub-list.
- **Exact vs. LIKE:** `identifier` matches exactly (case-sensitive); `identifier_filter` and `metadata_name` are case-insensitive and support SQL `LIKE`. `metadata_code` is matched by code (schema doesn't document case behaviour — likely exact, treat as such).
- **Filter at the source.** To get "AHUs at a site," use `Site.equipment(metadata_type_code: "AHU")` — don't fetch all equipment and filter client-side (it breaks under pagination).

---

## 3. Type reference

### Equipment & point types

**MetadataType** *(equipment-type layer; aka EquipmentType)*: The equipment category an instance belongs to (Air Handling Unit, Chiller, VAV). A lookup table, not a physical thing. Key fields: `type_id` (PK), `type` (display name), `type_code` (abbreviation). Referenced by both `Equipment.metadata_type_id` (the equipment's own type) and `Metadata.type_id` (the parent equipment type a point belongs to).

**Metadata** *(point-class layer; aka PointType)*: The class of a data point (sensor, setpoint, command). Key fields: `metadata_id` (PK), `type_id` (FK → MetadataType = parent equipment type), `name` (display), `code` (abbreviation), `type` (nested MetadataType), `unit`/`unit_id` (nested unit — note: singular). A single conceptual point class maps to multiple `metadata_id` values — one per parent equipment type (`type_id`).

**Equipment** *(equipment-instance layer)*: A single physical asset or system serving all or part of a building — a specific AHU, chiller, or VAV. Classified by exactly one MetadataType, located in a Zone. Key fields: `equipment_id` (PK), `metadata_type_id` (FK → MetadataType), `zone`/`zone_id` (nested location — the equipment's own location; its points may sit in different zones, see Favourite), `favourite_ids`/`favourites` (its data points). Filter by `metadata_type_id` to get all equipment of a type at a site.

**Favourite** *(point-instance layer)*: A single concrete data point on a piece of Equipment — the actual timeseries channel that carries readings. The instance of a Metadata point-class. Key fields: `fav_id` (PK — note: not `favourite_id`), `metadata_id` (FK → Metadata = the point's class/type), `equipment_id` (FK → Equipment = parent asset), `zone`/`zone_id` (nested Zone — the point's own location, may differ from the parent Equipment's zone), `identifier` (raw data stream identifier), `device_object`/`device_object_id` (nested BACnet Device Object), `history` (nested timeseries — takes `start`/`end`/`ts`/`latest`/`end_exclusive` args), `history_available` (Boolean — cheap check before pulling history). Each Favourite has exactly one Metadata and one Equipment.
- *BACnet branch:* a Favourite is either BACnet (has `device_object_id`) or non-BACnet (null `device_object_id`) — check `device_object_id` to branch.
- *Location:* a Favourite's `zone` is independent of its Equipment's `zone` — e.g. an AHU's per-duct sensors are zoned to the spaces each duct serves, not to the AHU's own location. For "points in zone X" queries, filter on `favourite.zone`, not `equipment.zone`; the point's level is `favourite.zone.level` (no direct `level` field).

### Location hierarchy

**Site** *(top location layer)*: A building. Hierarchy is Site → Level → Zone → Equipment. Key fields: `site_id` (PK), `site_name` (display name — note: not `name`), `city`/`state`/`country`, `timezone`, plus filterable nested collections: `zones`, `levels`, `equipment`, `favourites`, `devices`. No `site_id` shortcut on Equipment/Favourite — go via `zone.site_id`.

**Level** *(location layer)*: A floor of a Site, grouping the Zones on that floor (zero or more). Key fields: `level_id` (PK), `level_name` (display name — note: not `name`), `site_id` (FK → Site), `zones` (nested list). Hangs off `zone.level`, not off Equipment. Level has no `name` and no nested `level/level`.

**Zone** *(location layer)*: Where an Equipment/Favourite sits. Key fields: `zone_id` (PK), `site_id` (FK → Site — site is one hop from here, and is not a field on Equipment/Favourite), `level`/`level_id` (nested Level — needs subselection), `zone_name`/`zone_name_id`. Reach site via `equipment.zone.site_id` / `favourite.zone.site_id`.
- *Getting the zone's display name is two same-named hops:* the `zone_name` field on Zone is a nested **ZoneName object** (not a string), and the human-readable string is a field **also** called `zone_name` inside it. So the full path is `zone.zone_name.zone_name` (select as `zone_name { zone_name }`). `zone_name_id` is the FK to that ZoneName.

### BACnet layer

**Device** *(BACnet-controller layer; aka controller)*: The physical/networked BACnet controller that hosts DeviceObjects. Distinct from DeviceObject (an object on it) and Equipment (the asset it serves). Key fields: `device_id` (PK), `bacnet_device_name`/`bacnet_device_id` (BACnet identity), `model`, `vendor`, `virtual`, `device_objects` (nested list), `primary_equipment` (nested `[Equipment]` — the equipment this device is primary for). Note: no `name` — use `bacnet_device_name`.

**DeviceObject** *(BACnet-object layer; aka BACnet object)*: The raw BACnet object on a Device that a Favourite reads from — the protocol-level source of a point's values (the actual `analogInput`, `analogValue`, etc. on the controller). Distinct from the building "device is overloaded" sense: this is the BACnet device object. Key fields: `device_object_id` (PK), `device_id` (FK → Device = the physical/networked controller), `object_identifier` (BACnet object ID, e.g. `analog-input,1`), `object_name` (BACnet object name), `present_value` (last-scanned value), `units` (BACnet units — note: plural, unlike Metadata's singular `unit`), `description`. Back-reference: `favourite` (the Favourite bound to this object).
