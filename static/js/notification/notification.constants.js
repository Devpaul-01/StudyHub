// Notification type filter categories.
//
// Batch 2: these mirror _build_type_filter() in
// routes/student/notifications.py exactly — each key here is a value
// GET /profile/notifications/all and POST .../mark-all-read accept as
// their "type" param. Keeping this list in one place so the filter chips
// and any future notification UI don't have to repeat these string
// literals (and drift from the backend) in multiple files.
export const NOTIFICATION_TYPES = [
  { value: '',            label: 'All' },
  { value: 'engagements',  label: 'Engagement' },
  { value: 'badges',       label: 'Badges' },
  { value: 'connections',  label: 'Connections' },
  { value: 'threads',      label: 'Threads' },
  { value: 'messages',     label: 'Messages' },
  { value: 'mentions',     label: 'Mentions' },
];
