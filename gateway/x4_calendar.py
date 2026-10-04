"""Pure calendar display-consent projection; no collector or private-source imports."""


def project_events(events, fields, *, retained=False, previous_fields=()):
    approved = []
    for event in events[:256]:
        if not isinstance(event, dict):
            continue
        row = {}
        if retained:
            title_field = event.get('_title_field')
            if title_field in fields and title_field in previous_fields and event.get('summary'):
                value = event['summary']
                row.update(summary=' '.join(value.split())[:160] if isinstance(value, str) else '',
                           _title_field=title_field)
        else:
            for field in fields:
                if field in ('summary', 'text', 'displayable_text', 'name', 'activity', 'health') and event.get(field):
                    value = event[field]
                    row.update(summary=' '.join(value.split())[:160] if isinstance(value, str) else '',
                               _title_field=field)
                    break
        if any(field in fields for field in ('start', 'at')):
            for key in ('start', 'end'):
                value = event.get(key) if key == 'end' or retained else event.get('start', event.get('at'))
                if isinstance(value, dict):
                    value = value.get('dateTime', value.get('date'))
                if isinstance(value, str) and len(value) <= 40:
                    row[key] = value
        if row.get('summary') or row.get('start'):
            approved.append(row)
    return approved
