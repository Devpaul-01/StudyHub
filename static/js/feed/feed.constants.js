
  /**
 * ============================================================================
 * FEED CONSTANTS
 * All static configuration and constant data
 * ============================================================================
 */

export const FEED_FILTERS = {
  ALL: 'all',
  DEPARTMENT: 'department',
  TRENDING: 'trending',
  CONNECTIONS: 'connections',
  UNSOLVED: 'unsolved'
};

export const POST_TYPES = {
  QUESTION: 'question',
  PROBLEM: 'problem',
  DISCUSSION: 'discussion',
  RESOURCE: 'resource',
  ANNOUNCEMENT: 'announcement'
};

export const REACTION_TYPES = {
  like: '👍',
  love: '❤️',
  helpful: '💡',
  fire: '🔥',
  wow: '🤯',
  celebrate: '🎉',
  laugh: '😂',
  solution: '🧠'
};

export const POST_TYPE_ICONS = {
  question: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>',
  problem: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>',
  discussion: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>',
  resource: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>',
  announcement: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>'
};

export const AVAILABLE_TAGS = [
  "Accounting", "Acting", "Aerospace Engineering", "African Studies", "Agricultural Science", 
  "Algebra", "Android Development", "Animation", "Anthropology", "Applied Mathematics", 
  "Archaeology", "Architecture", "Art History", "Artificial Intelligence", 
  "Artificial Intelligence Subfields", "AR/VR Learning", "Astronomy", "Automotive Engineering", 
  "Audiology", "Biochemistry", "Bioinformatics", "Biology", "Biomedical Engineering", 
  "Blockchain", "Botany", "Business Administration", "Business Analytics", "Career Advice", 
  "Chemical Engineering", "Chemistry", "Civil Engineering", "Cloud Computing", "College Life", 
  "Commerce", "Communication Studies", "Computer Engineering", "Computer Science", 
  "Construction Management", "Content Creation", "Creative Writing", "Criminology", 
  "Cultural Studies", "Cybersecurity", "Dance", "Data Analysis", "Data Engineering", 
  "Data Science", "Data Visualization", "Database Management", "Dart", "Dentistry", 
  "Dermatology", "Design Thinking", "Digital Art", "Digital Marketing", 
  "Digital Productivity Tools", "Drama", "Drawing", "Early Childhood Education", "Ecology", 
  "Economics", "Education", "Electrical Engineering", "Electronics Engineering", 
  "Emergency Medicine", "Embedded Systems", "Emotional Intelligence", "Engineering", 
  "English Language", "Entrepreneurship", "Environmental Science", "Epidemiology", "Ethics", 
  "Excel Skills", "Fashion Design", "Film Production", "Finance", "Fine Art", "Food Science", 
  "Forensic Science", "French Language", "Game Development", "Gender Studies", "Genetics", 
  "Geography", "Geology", "Geometry", "Graphic Design", "Health Education", "Health Science", 
  "History", "Homeschooling", "Hospitality Management", "Human Anatomy", "Human Resources", 
  "Human Rights", "Human Physiology", "Immunology", "Industrial Engineering", 
  "Information Technology", "International Relations", "iOS Development", "Journalism", 
  "Java", "JavaScript", "Kindergarten Education", "Kotlin", "Languages", "Law", "Leadership", 
  "Learning Analytics", "Learning Disabilities", "Linguistics", "Literature", 
  "Machine Learning", "Machine Learning Subfields", "Marine Biology", "Marketing", 
  "Mathematics", "Mechanical Engineering", "Mechatronics Engineering", "Media Studies", 
  "Medical Laboratory Science", "Medicine and Surgery", "Mental Health", "Microbiology", 
  "Mobile App Development", "Molecular Biology", "Moral Philosophy", "Music", 
  "Network Engineering", "Neuroscience", "Nursing", "Nutrition", "Online Learning", 
  "Occupational Therapy", "Optometry", "Pathology", "Pediatric Medicine", "Performing Arts", 
  "Petroleum Engineering", "Pharmacy", "Philosophy", "Photography", "Physical Education", 
  "Physical Therapy", "Physics", "Physiology", "PHP", "Pre-Med", "Primary Education", 
  "Product Design", "Project Management", "Psychiatry", "Psychology", "Public Administration", 
  "Public Health", "Public Speaking", "Python Libraries", "Python Programming", "Radiology", 
  "React Development", "React Native", "Reading Skills", "R Programming", "Research Methods", 
  "Robotics", "Ruby", "Rust", "Scholarship Opportunities", "Science Education", 
  "Simulation-Based Learning", "Sculpture", "Secondary Education", "Self-Study", 
  "Social Sciences", "Sociology", "Software Development", "Software Engineering", 
  "Spanish Language", "Special Education", "SQL", "Statistics", "STEM", "Study Abroad", 
  "Study Tips", "Surgery", "Swift", "Teacher Training", "Theology", "Theatre Studies", 
  "TOEFL Prep", "TypeScript", "UI Design", "UI/UX Design", "URDU Language", 
  "Veterinary Medicine", "Virtual Learning", "Visual Arts", "VR Learning", "Web Development", 
  "Web Frameworks", "Wildlife Biology", "Writing Skills", "XR Learning", "Zoology"
];

export const CAN_SOLVE_TYPES = ["problem", "question"];

export const MAX_TAGS = 5;
export const MAX_DISPLAY_RESOURCES = 4;
export const MAX_COMMENT_PREVIEW_RESOURCES = 3;
export const LONG_PRESS_TIME = 800;
export const PULL_TO_REFRESH_THRESHOLD = 80;

export const WIDGET_ORDER = [
  { id: 'suggestedConnections', every: 3 },
  { id: 'popularTags', every: 5 },
  { id: 'risingStars', every: 4 },
  { id: 'openThreads', every: 6 },
  { id: 'canHelp', every: 8 },
  { id: 'topBadgeEarners', every: 9 }
];
