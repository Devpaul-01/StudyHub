/**
 * ============================================================================
 * HOMEWORK INITIALIZATION
 * Sets up homework section when navigated to
 * ============================================================================
 */

import { initializeHomeworkSection } from './homework.render.js';
import { loadStreakData } from './homework.streak.js';
import { loadChampions } from './homework.champions.js';


// Flag to prevent multiple initializations
let isHomeworkInitialized = false;

/**
 * Initialize homework module
 */
export async function initHomework() {
  // Prevent multiple initializations
  if (isHomeworkInitialized) {
    console.log('⚠️ Homework already initialized, skipping...');
    return;
  }

  console.log('🎓 Initializing homework section...');
  isHomeworkInitialized = true;

  try {
    // Wait for DOM to be ready
    if (document.readyState === 'loading') {
      await new Promise(resolve => {
        document.addEventListener('DOMContentLoaded', resolve);
      });
    }

    // Initialize the homework section
    await initializeHomeworkSection();
    await loadStreakData();

    console.log('✅ Homework section initialized');
  } catch (error) {
    console.error('❌ Error initializing homework:', error);
    isHomeworkInitialized = false; // Reset flag on error so retry is possible
  }
}

/**
 * Setup mutation observer to watch for homework section activation
 */
function setupHomeworkObserver() {
  const homeworkSection = document.getElementById('homework');

  if (!homeworkSection) {
    console.warn('⚠️ Homework section not found, observer not set up');
    return;
  }

  // Check if homework section is already active on page load
  if (homeworkSection.classList.contains('active')) {
    console.log('📍 Homework section already active, initializing...');
    initHomework();
  }

  // Set up observer for future tab switches
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      if (mutation.attributeName === 'class') {
        if (homeworkSection.classList.contains('active')) {
          initHomework();
        }
      }
    });
  });

  observer.observe(homeworkSection, {
    attributes: true,
    attributeFilter: ['class']
  });

  console.log('👀 Homework observer set up');
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded',setupHomeworkObserver);
} else {
  setupHomeworkObserver();
}
