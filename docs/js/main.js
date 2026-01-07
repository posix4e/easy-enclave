// EASYENCLAVE - Cyberpunk Effects

// Random glitch effect on title
function glitchTitle() {
  const title = document.querySelector('.hero-title');
  if (!title) return;

  setInterval(() => {
    if (Math.random() > 0.95) {
      title.style.transform = `translate(${Math.random() * 4 - 2}px, ${Math.random() * 4 - 2}px)`;
      setTimeout(() => {
        title.style.transform = 'translate(0, 0)';
      }, 50);
    }
  }, 100);
}

// Typing effect for install box
function typeEffect() {
  const installBox = document.querySelector('.install-box code');
  if (!installBox) return;

  const text = installBox.textContent;
  installBox.textContent = '';
  let i = 0;

  const type = () => {
    if (i < text.length) {
      installBox.textContent += text.charAt(i);
      i++;
      setTimeout(type, 50 + Math.random() * 50);
    }
  };

  // Start after a small delay
  setTimeout(type, 500);
}

// Copy to clipboard for code blocks
function setupCopyButtons() {
  document.querySelectorAll('pre').forEach(pre => {
    pre.style.cursor = 'pointer';
    pre.title = 'Click to copy';

    pre.addEventListener('click', async () => {
      const code = pre.querySelector('code');
      const text = code ? code.textContent : pre.textContent;

      try {
        await navigator.clipboard.writeText(text);

        // Visual feedback
        const original = pre.style.borderColor;
        pre.style.borderColor = '#39ff14';
        pre.style.boxShadow = '0 0 20px rgba(57, 255, 20, 0.3)';

        setTimeout(() => {
          pre.style.borderColor = original;
          pre.style.boxShadow = '';
        }, 500);
      } catch (err) {
        console.error('Copy failed:', err);
      }
    });
  });
}

// Smooth scroll for anchor links
function setupSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      e.preventDefault();
      const target = document.querySelector(this.getAttribute('href'));
      if (target) {
        target.scrollIntoView({ behavior: 'smooth' });
      }
    });
  });
}

// Random neon flicker effect
function neonFlicker() {
  const elements = document.querySelectorAll('.hero-title, .logo');

  elements.forEach(el => {
    setInterval(() => {
      if (Math.random() > 0.98) {
        el.style.opacity = '0.8';
        setTimeout(() => {
          el.style.opacity = '1';
        }, 50);
      }
    }, 100);
  });
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  glitchTitle();
  typeEffect();
  setupCopyButtons();
  setupSmoothScroll();
  neonFlicker();
});
