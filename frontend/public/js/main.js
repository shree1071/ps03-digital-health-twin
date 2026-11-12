(function () {
  "use strict";

  if (!window.gsap || !window.ScrollTrigger) return;
  gsap.registerPlugin(ScrollTrigger);

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------- smooth scroll ---------------- */
  var lenis = null;
  if (window.Lenis && !reduce) {
    lenis = new Lenis({ lerp: 0.1 });
    lenis.on("scroll", ScrollTrigger.update);
    gsap.ticker.add(function (time) {
      lenis.raf(time * 1000);
    });
    gsap.ticker.lagSmoothing(0);
  }

  function scrollToEl(el, offset) {
    if (!el) return;
    if (lenis) lenis.scrollTo(el, { offset: offset || 0 });
    else {
      var y = el.getBoundingClientRect().top + window.scrollY + (offset || 0);
      window.scrollTo({ top: y, behavior: "smooth" });
    }
  }

  function qs(root, sel) {
    return Array.prototype.slice.call(root.querySelectorAll(sel));
  }

  function visible(root) {
    var els = qs(root, "[data-anim]");
    if (els.length) gsap.set(els, { visibility: "visible" });
  }

  /* ---------------- mobile menu ---------------- */
  var header = document.querySelector("header");
  var menuButton = header ? header.querySelector("button[aria-label]") : null;
  var menuWrap = document.querySelector('div[aria-hidden][inert]');
  var menuPanel = menuWrap ? menuWrap.firstElementChild : null;
  var menuItems = menuWrap ? qs(menuWrap, "[data-menu-item]") : [];
  var menuTails = menuWrap ? qs(menuWrap, "[data-menu-tail]") : [];
  var menuOpen = false;
  var menuTl = null;

  function lockScroll() {
    if (lenis) lenis.stop();
    document.documentElement.classList.add("lenis-locked");
    document.body.style.overflow = "hidden";
  }
  function unlockScroll() {
    if (lenis) lenis.start();
    document.documentElement.classList.remove("lenis-locked");
    document.body.style.overflow = "";
  }

  function setMenuButton(open) {
    if (!menuButton) return;
    menuButton.setAttribute("aria-expanded", open ? "true" : "false");
    menuButton.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    var bars = menuButton.querySelectorAll("span");
    bars[0].className =
      "absolute h-px w-4 bg-current transition-transform duration-500 [transition-timing-function:var(--ease-brand)] " +
      (open ? "translate-y-0 rotate-45" : "-translate-y-[3px]");
    bars[1].className =
      "absolute h-px w-4 bg-current transition-transform duration-500 [transition-timing-function:var(--ease-brand)] " +
      (open ? "translate-y-0 -rotate-45" : "translate-y-[3px]");
  }

  function openMenu() {
    if (menuOpen || !menuWrap) return;
    menuOpen = true;
    setMenuButton(true);
    lockScroll();
    menuWrap.style.pointerEvents = "auto";
    menuWrap.setAttribute("aria-hidden", "false");
    menuWrap.removeAttribute("inert");
    if (menuTl) menuTl.kill();
    menuTl = gsap.timeline();
    menuTl
      .fromTo(
        menuPanel,
        { clipPath: "inset(0% 0% 100% 0%)" },
        { clipPath: "inset(0% 0% 0% 0%)", duration: 0.75, ease: "expo.inOut" }
      )
      .fromTo(
        menuItems,
        { yPercent: 115, opacity: 0 },
        { yPercent: 0, opacity: 1, duration: 0.85, stagger: 0.07, ease: "expo.out" },
        "-=0.38"
      )
      .fromTo(
        menuTails,
        { y: 24, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.7, stagger: 0.08, ease: "expo.out" },
        "-=0.5"
      );
  }

  function closeMenu(instant) {
    if (!menuOpen || !menuWrap) return;
    menuOpen = false;
    setMenuButton(false);
    unlockScroll();
    if (menuTl) menuTl.kill();
    if (instant) {
      gsap.set(menuPanel, { clipPath: "inset(0% 0% 100% 0%)" });
      menuWrap.style.pointerEvents = "none";
      menuWrap.setAttribute("aria-hidden", "true");
      menuWrap.setAttribute("inert", "");
      return;
    }
    menuTl = gsap.timeline({
      onComplete: function () {
        menuWrap.style.pointerEvents = "none";
        menuWrap.setAttribute("aria-hidden", "true");
        menuWrap.setAttribute("inert", "");
      },
    });
    menuTl
      .to(menuItems, { yPercent: -60, opacity: 0, duration: 0.3, stagger: 0.03, ease: "power2.in" })
      .to(menuTails, { opacity: 0, duration: 0.2 }, 0)
      .to(menuPanel, { clipPath: "inset(0% 0% 100% 0%)", duration: 0.6, ease: "expo.inOut" }, "-=0.1");
  }

  if (menuButton) {
    menuButton.addEventListener("click", function () {
      if (menuOpen) closeMenu();
      else openMenu();
    });
  }
  if (menuWrap) {
    qs(menuWrap, "[data-menu-item]").forEach(function (a) {
      a.addEventListener("click", function () {
        closeMenu(true);
      });
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeMenu();
  });

  /* header behaviour */
  if (header) {
    if (!reduce) {
      gsap.fromTo(
        header,
        { y: -80, opacity: 0 },
        { y: 0, opacity: 1, duration: 1.1, delay: 0.15, ease: "expo.out" }
      );
    }
    var lastY = window.scrollY;
    var hidden = false;
    window.addEventListener(
      "scroll",
      function () {
        var y = window.scrollY;
        var delta = y - lastY;
        if (Math.abs(delta) < 6) return;
        var shouldHide = delta > 0 && y > 220;
        if (shouldHide === hidden || menuOpen) {
          lastY = y;
          return;
        }
        hidden = shouldHide;
        gsap.to(header, {
          y: shouldHide ? -120 : 0,
          opacity: shouldHide ? 0 : 1,
          duration: 0.55,
          ease: "power3.out",
          overwrite: true,
        });
        lastY = y;
      },
      { passive: true }
    );
  }

  /* ---------------- hero ---------------- */
  var hero = document.getElementById("hero");
  if (hero) {
    visible(hero);
    var heroTl = gsap.timeline({ defaults: { ease: "expo.out" } });
    heroTl
      .fromTo(hero.querySelector("[data-hero-image]"), { scale: 1.16 }, { scale: 1, duration: 1.8 }, 0)
      .fromTo(hero.querySelector("[data-hero-scrim]"), { opacity: 0.35 }, { opacity: 1, duration: 1.4 }, 0)
      .fromTo(
        hero.querySelector("[data-hero-badge]"),
        { clipPath: "inset(0% 100% 0% 0% round 999px)" },
        { clipPath: "inset(0% 0% 0% 0% round 999px)", duration: 0.9 },
        0.35
      )
      .fromTo(hero.querySelector("[data-hero-dot]"), { scale: 0 }, { scale: 1, duration: 0.7 }, 0.67)
      .fromTo(hero.querySelectorAll("[data-word]"), { yPercent: 118 }, { yPercent: 0, duration: 1.15, stagger: 0.07 }, 0.5)
      .fromTo(hero.querySelector("[data-hero-sub]"), { x: 30, opacity: 0 }, { x: 0, opacity: 1, duration: 0.95 }, 0.9)
      .fromTo(hero.querySelector("[data-hero-cta]"), { x: 24, opacity: 0 }, { x: 0, opacity: 1, duration: 0.9 }, 1.02)
      .fromTo(
        hero.querySelectorAll("[data-hero-trust]"),
        { x: 18, opacity: 0 },
        { x: 0, opacity: 1, duration: 0.85, stagger: 0.08 },
        1.12
      )
      .fromTo(
        hero.querySelector("[data-hero-social]"),
        { y: -16, opacity: 0, scale: 0.94 },
        { y: 0, opacity: 1, scale: 1, duration: 0.8 },
        0.95
      )
      .fromTo(
        hero.querySelectorAll("[data-hero-avatar]"),
        { scale: 0, opacity: 0 },
        { scale: 1, opacity: 1, duration: 0.5, stagger: 0.08 },
        1.1
      )
      .fromTo(
        hero.querySelectorAll("[data-hero-card], [data-hero-card-mobile]"),
        { x: 56, opacity: 0 },
        { x: 0, opacity: 1, duration: 1.1 },
        1.3
      )
      .fromTo(
        hero.querySelector("[data-hero-cue]"),
        { opacity: 0, y: -8 },
        {
          opacity: 1,
          y: 0,
          duration: 0.7,
          onComplete: function () {
            gsap.to(hero.querySelector("[data-hero-cue]"), {
              y: 8,
              duration: 1.1,
              ease: "sine.inOut",
              repeat: -1,
              yoyo: true,
            });
          },
        },
        1.7
      );

    gsap.fromTo(
      hero.querySelector("[data-hero-image]"),
      { yPercent: 0, scale: 1 },
      {
        yPercent: -8,
        scale: 1.08,
        ease: "none",
        scrollTrigger: { trigger: hero, start: "top top", end: "bottom top", scrub: 0.5 },
      }
    );
    gsap.to(hero.querySelector("[data-hero-scrim-scroll]"), {
      opacity: 0.55,
      ease: "none",
      scrollTrigger: { trigger: hero, start: "top top", end: "70% top", scrub: 0.4 },
    });
    gsap.to(hero.querySelector("[data-hero-copy]"), {
      y: -64,
      opacity: 0.12,
      ease: "none",
      scrollTrigger: { trigger: hero, start: "top top", end: "65% top", scrub: 0.4 },
    });
    gsap.to(hero.querySelector("[data-hero-cue]"), {
      opacity: 0,
      ease: "none",
      scrollTrigger: { trigger: hero, start: "top top", end: "15% top", scrub: 0.3 },
    });
  }

  /* ---------------- intro / video ---------------- */
  var intro = document.getElementById("experiences");
  if (intro) {
    var introTrigger = function () {
      return {
        trigger: intro.querySelector("[data-reveal-copy]"),
        start: "top 78%",
        end: "bottom 58%",
        scrub: 0.4,
      };
    };
    gsap.to(intro.querySelectorAll("[data-reveal-token]"), {
      opacity: 1,
      ease: "none",
      stagger: 0.32,
      scrollTrigger: introTrigger(),
    });
    gsap.fromTo(
      intro.querySelector("[data-intro-rule]"),
      { scaleY: 0 },
      { scaleY: 1, ease: "none", transformOrigin: "top center", scrollTrigger: introTrigger() }
    );
    gsap.fromTo(
      intro.querySelectorAll("[data-intro-rail]"),
      { y: 18, opacity: 0 },
      {
        y: 0,
        opacity: 1,
        duration: 0.85,
        stagger: 0.12,
        ease: "expo.out",
        scrollTrigger: { trigger: intro.querySelector("[data-reveal-copy]"), start: "top 86%" },
      }
    );
    gsap.fromTo(
      intro.querySelector("[data-video-frame]"),
      { clipPath: "inset(0% 28% 0% 28% round 40px)" },
      {
        clipPath: "inset(0% 0% 0% 0% round 16px)",
        ease: "none",
        scrollTrigger: { trigger: intro.querySelector("[data-video-frame]"), start: "top 94%", end: "top 26%", scrub: 0.5 },
      }
    );
    gsap.fromTo(
      intro.querySelector("[data-video-inner]"),
      { yPercent: -7, scale: 1.16 },
      {
        yPercent: 7,
        scale: 1.16,
        ease: "none",
        scrollTrigger: {
          trigger: intro.querySelector("[data-video-frame]"),
          start: "top bottom",
          end: "bottom top",
          scrub: 0.6,
        },
      }
    );

    var video = intro.querySelector("[data-video-inner]");
    var videoBtn = intro.querySelector("[data-video-frame] button");
    var playIcon =
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-play size-4 translate-x-[1px] md:size-5"><polygon points="6 3 20 12 6 21 6 3"></polygon></svg>';
    var pauseIcon =
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-pause size-4 md:size-5"><rect x="14" y="4" width="4" height="16" rx="1"></rect><rect x="6" y="4" width="4" height="16" rx="1"></rect></svg>';
    if (videoBtn && video) {
      var pill = videoBtn.firstElementChild;
      var iconWrap = pill.firstElementChild;
      var textEl = pill.lastElementChild;
      videoBtn.addEventListener("click", async function () {
        if (video.paused) {
          try {
            await video.play();
          } catch (err) {
            video.muted = true;
            try {
              await video.play();
            } catch (e) {}
          }
          var playing = !video.paused;
          iconWrap.innerHTML = playing ? pauseIcon : playIcon;
          textEl.textContent = playing ? "Pause" : "Watch the film";
          pill.classList.toggle("opacity-0", playing);
          pill.classList.toggle("group-hover:opacity-100", playing);
          pill.classList.toggle("opacity-100", !playing);
          videoBtn.setAttribute("aria-label", playing ? "Pause the video" : "Play the video");
        } else {
          video.pause();
          iconWrap.innerHTML = playIcon;
          textEl.textContent = "Watch the film";
          pill.classList.remove("opacity-0", "group-hover:opacity-100");
          pill.classList.add("opacity-100");
          videoBtn.setAttribute("aria-label", "Play the video");
        }
      });
      video.addEventListener("ended", function () {
        iconWrap.innerHTML = playIcon;
        textEl.textContent = "Watch the film";
      });
    }
  }

  /* ---------------- familiar marquee ---------------- */
  var familiar = document.getElementById("familiar");
  if (familiar) {
    visible(familiar);
    var famTl = gsap.timeline({
      scrollTrigger: { trigger: familiar, start: "top 80%" },
      defaults: { ease: "expo.out" },
    });
    famTl.fromTo(
      familiar.querySelectorAll("[data-word]"),
      { yPercent: 115 },
      { yPercent: 0, duration: 1.05, stagger: 0.055 }
    );

    var track = familiar.querySelector(".mt-12 > div");
    if (track && !reduce) {
      var marquee = gsap.to(track, { xPercent: -50, duration: 46, ease: "none", repeat: -1 });
      ScrollTrigger.create({
        trigger: familiar,
        start: "top bottom",
        end: "bottom top",
        onUpdate: function (self) {
          var v = gsap.utils.clamp(-7, 7, self.getVelocity() / 260);
          gsap.to(marquee, { timeScale: v >= 0 ? Math.max(1, v) : Math.min(-1, v), duration: 0.25, overwrite: true });
          gsap.to(marquee, { timeScale: 1, duration: 1.1, delay: 0.3, overwrite: "auto" });
        },
      });
      track.addEventListener("pointerenter", function () {
        gsap.to(marquee, { timeScale: 0, duration: 0.5, overwrite: true });
      });
      track.addEventListener("pointerleave", function () {
        gsap.to(marquee, { timeScale: 1, duration: 0.8, overwrite: true });
      });
    }
  }

  /* ---------------- services showcase (home tabs) ---------------- */
  var SERVICES = [
    {
      id: "one-to-one-therapy",
      title: "One-to-One Therapy",
      body: "Regular sessions to look at the pressures and habits that have quietly become your normal.",
      price: "$85/session",
      tags: ["ANXIETY", "OVERTHINKING", "SELF-DOUBT", "BIG CHANGES", "OLD PATTERNS"],
      image: "images/service-individual.jpg",
    },
    {
      id: "anxiety-and-overthinking",
      title: "Anxiety and Overthinking",
      body: "Understand the cycle underneath constant worry, racing thoughts and the need to be certain.",
      price: "$85/session",
      tags: ["CONSTANT WORRY", "RACING THOUGHTS", "UNCERTAINTY", "AVOIDANCE", "BROKEN SLEEP"],
      image: "images/service-anxiety.jpg",
    },
    {
      id: "burnout-and-exhaustion",
      title: "Burnout and Exhaustion",
      body: "For when long pressure has worn down your energy, your focus and your patience with yourself.",
      price: "$85/session",
      tags: ["WORK PRESSURE", "RUNNING ON EMPTY", "BOUNDARIES", "LOST MOTIVATION", "RECOVERY"],
      image: "images/service-stress.jpg",
    },
    {
      id: "couples-and-relationships",
      title: "Couples and Relationships",
      body: "A structured space for individuals or couples to work through recurring conflict and say things clearly.",
      price: "$85/session",
      tags: ["COUPLES", "COMMUNICATION", "TRUST", "BOUNDARIES", "THE SAME ARGUMENT"],
      image: "images/service-relationship.jpg",
    },
  ];

  var servicesSection = document.getElementById("services");
  if (servicesSection) {
    visible(servicesSection);
    var stage = servicesSection.querySelector("[data-stage]");
    var rows = qs(servicesSection, "[data-menu-row]");
    var current = 0;

    var svcTl = gsap.timeline({
      scrollTrigger: { trigger: servicesSection, start: "top 78%" },
      defaults: { ease: "expo.out" },
    });
    svcTl
      .fromTo(
        servicesSection.querySelectorAll("[data-word]"),
        { yPercent: 115 },
        { yPercent: 0, duration: 1.05, stagger: 0.05 }
      )
      .fromTo(
        servicesSection.querySelector("[data-show-cta]"),
        { y: 20, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.8 },
        "-=0.7"
      )
      .fromTo(
        rows,
        { x: -26, opacity: 0 },
        { x: 0, opacity: 1, duration: 0.8, stagger: 0.07 },
        "-=0.6"
      )
      .fromTo(stage, { y: 46, opacity: 0 }, { y: 0, opacity: 1, duration: 1 }, "-=0.5");
    if (reduce) svcTl.progress(1);

    function cardMarkup(s) {
      return (
        '<div class="relative aspect-[4/3] overflow-hidden md:aspect-auto">' +
        '<img data-card-image src="' +
        s.image +
        '" alt="" loading="lazy" class="size-full object-cover will-change-transform"/>' +
        "</div>" +
        '<div class="flex flex-col p-6 md:p-10">' +
        '<h3 data-card-part class="font-display text-[24px] leading-[1.12] text-ink md:text-[30px]">' +
        s.title +
        "</h3>" +
        '<p data-card-part class="mt-3 max-w-[46ch] text-[15px] leading-[1.6] text-slateleaf">' +
        s.body +
        "</p>" +
        '<div data-card-part class="mt-8 border-t border-ink/10 pt-5">' +
        '<p class="eyebrow text-mist">Starting price</p>' +
        '<p class="font-display mt-1 text-[24px] text-ink md:text-[28px]">' +
        s.price +
        "</p></div>" +
        '<ul data-card-part class="mt-auto flex flex-wrap gap-2 pt-8">' +
        s.tags
          .map(function (t) {
            return (
              '<li data-card-tag class="rounded-full border border-ink/12 px-3 py-1.5 text-[10px] font-medium tracking-[0.12em] text-slateleaf">' +
              t +
              "</li>"
            );
          })
          .join("") +
        "</ul></div>"
      );
    }

    function setRowActive(row, active) {
      var spans = row.querySelectorAll("button > span");
      var num = spans[0];
      var label = spans[1];
      var arrow = row.querySelector("svg");
      row.querySelector("button").setAttribute("aria-current", active ? "true" : "false");
      num.className =
        "mt-0.5 text-[12px] tabular-nums transition-colors duration-500 " + (active ? "text-ink" : "text-mist");
      label.className =
        "flex-1 text-[15px] leading-[1.35] transition-colors duration-500 [transition-timing-function:var(--ease-brand)] " +
        (active ? "text-ink" : "text-mist group-hover:text-ink");
      arrow.setAttribute(
        "class",
        "lucide lucide-arrow-up-right mt-0.5 size-4 shrink-0 transition-all duration-500 [transition-timing-function:var(--ease-brand)] " +
          (active
            ? "translate-x-0 text-ink opacity-100"
            : "-translate-x-1 text-mist opacity-0 group-hover:translate-x-0 group-hover:opacity-100")
      );
    }

    function swapStage(index) {
      if (index === current) return;
      current = index;
      rows.forEach(function (row, i) {
        setRowActive(row, i === index);
      });
      stage.innerHTML = cardMarkup(SERVICES[index]);
      var tl = gsap.timeline({ defaults: { ease: "expo.out" } });
      tl.fromTo(stage, { y: 34, opacity: 0 }, { y: 0, opacity: 1, duration: 0.85 })
        .fromTo(stage.querySelector("[data-card-image]"), { scale: 1.08 }, { scale: 1, duration: 1.4 }, 0)
        .fromTo(
          stage.querySelectorAll("[data-card-part]"),
          { y: 22, opacity: 0 },
          { y: 0, opacity: 1, duration: 0.7, stagger: 0.07 },
          0.12
        )
        .fromTo(
          stage.querySelectorAll("[data-card-tag]"),
          { y: 12, opacity: 0 },
          { y: 0, opacity: 1, duration: 0.5, stagger: 0.035 },
          0.32
        );
      if (reduce) tl.progress(1);
    }

    rows.forEach(function (row, i) {
      row.querySelector("button").addEventListener("click", function () {
        if (stage) {
          swapStage(i);
          return;
        }
        rows.forEach(function (r, j) {
          setRowActive(r, j === i);
        });
        scrollToEl(document.getElementById(SERVICES[i].id), -140);
      });
    });

    // services page list mode
    var serviceCards = qs(document, "[data-service-card]");
    serviceCards.forEach(function (card) {
      gsap.fromTo(
        card,
        { y: 70, opacity: 0 },
        { y: 0, opacity: 1, duration: 1.05, ease: "expo.out", scrollTrigger: { trigger: card, start: "top 85%" } }
      );
      var img = card.querySelector("[data-card-image]");
      if (img) {
        gsap.fromTo(
          img,
          { scale: 1.16 },
          {
            scale: 1,
            ease: "none",
            scrollTrigger: { trigger: card, start: "top bottom", end: "bottom top", scrub: 0.6 },
          }
        );
      }
    });
  }

  /* ---------------- principle chapter ---------------- */
  var chapter = document.querySelector(".bg-ink > .pointer-events-none.absolute");
  var chapterWrap = document.querySelector("[data-chapter-bg]");
  if (chapterWrap) {
    var wrap = chapterWrap.parentElement.parentElement;
    gsap.fromTo(
      chapterWrap,
      { yPercent: -8, scale: 1.18 },
      {
        yPercent: 8,
        scale: 1,
        ease: "none",
        scrollTrigger: { trigger: wrap, start: "top bottom", end: "bottom top", scrub: 0.6 },
      }
    );
    gsap.fromTo(
      document.querySelector("[data-chapter-wash]"),
      { opacity: 0.99 },
      {
        opacity: 0.93,
        ease: "none",
        scrollTrigger: { trigger: wrap, start: "top center", end: "center center", scrub: true },
      }
    );
  }

  var PRINCIPLES = [
    {
      title: "Warmth before assessment",
      body: "You are met as a person having a hard time, not a case to be worked out. Nothing you bring will be treated as too small or too much.",
      image: "images/principle-warmth.jpg",
    },
    {
      title: "Privacy you can count on",
      body: "What you say in a session stays in that room. Your records are handled with care, discretion and full professional confidentiality.",
      image: "images/principle-privacy.jpg",
    },
    {
      title: "Clarity over quick fixes",
      body: "We work towards understanding what is actually happening rather than rushing to a technique, so the change you make holds.",
      image: "images/principle-clarity.jpg",
    },
    {
      title: "Steadiness, at your pace",
      body: "What you need shifts over time. Sessions are reviewed openly and often, so support keeps matching where you actually are.",
      image: "images/principle-steadiness.jpg",
    },
  ];

  var principles = document.getElementById("principles");
  if (principles) {
    visible(principles);
    var pTabs = qs(principles, "[data-principle-tab]");
    var pPanel = principles.querySelector("[data-principle-panel]");
    var pImage = principles.querySelector("[data-principle-image]");
    var pCopy = qs(principles, "[data-principle-copy]");
    var pIndex = 0;

    var pTl = gsap.timeline({
      scrollTrigger: { trigger: principles, start: "top 76%" },
      defaults: { ease: "expo.out" },
    });
    pTl
      .fromTo(principles.querySelectorAll("[data-word]"), { yPercent: 115 }, { yPercent: 0, duration: 1.05, stagger: 0.05 })
      .fromTo(pTabs, { y: 22, opacity: 0 }, { y: 0, opacity: 1, duration: 0.7, stagger: 0.06 }, "-=0.65")
      .fromTo(pPanel, { y: 54, opacity: 0 }, { y: 0, opacity: 1, duration: 1.1 }, "-=0.5")
      .fromTo(
        pImage,
        { clipPath: "inset(0% 100% 0% 0%)", scale: 1.12 },
        { clipPath: "inset(0% 0% 0% 0%)", scale: 1, duration: 1.2 },
        "-=0.9"
      )
      .fromTo(pCopy, { y: 26, opacity: 0 }, { y: 0, opacity: 1, duration: 0.75, stagger: 0.08 }, "-=0.9");
    if (reduce) pTl.progress(1);

    function setPrinciple(index) {
      if (index === pIndex) return;
      pIndex = index;
      pTabs.forEach(function (tab, i) {
        var active = i === index;
        tab.setAttribute("aria-selected", active ? "true" : "false");
        tab.className =
          "rounded-full border px-5 py-2.5 text-[14px] transition-all duration-500 [transition-timing-function:var(--ease-brand)] " +
          (active
            ? "border-ember bg-ember text-ink"
            : "border-white/15 text-line hover:border-white/40 hover:text-white");
      });
      var p = PRINCIPLES[index];
      pImage.setAttribute("src", p.image);
      var h3 = pPanel.querySelector("h3");
      var para = pPanel.querySelector("[data-principle-copy] + p");
      if (h3) h3.textContent = p.title;
      if (para) para.textContent = p.body;
      var tl = gsap.timeline({ defaults: { ease: "expo.out" } });
      tl.fromTo(
        pImage,
        { clipPath: "inset(0% 100% 0% 0%)", scale: 1.12 },
        { clipPath: "inset(0% 0% 0% 0%)", scale: 1, duration: 1.15 }
      ).fromTo(pCopy, { y: 26, opacity: 0 }, { y: 0, opacity: 1, duration: 0.75, stagger: 0.08 }, 0.22);
      if (reduce) tl.progress(1);
    }

    pTabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () {
        setPrinciple(i);
      });
    });
  }

  /* ---------------- case studies ---------------- */
  var caseStudies = document.getElementById("case-studies");
  if (caseStudies) {
    visible(caseStudies);
    var caseTl = gsap.timeline({
      scrollTrigger: { trigger: caseStudies, start: "top 80%" },
      defaults: { ease: "expo.out" },
    });
    caseTl
      .fromTo(caseStudies.querySelectorAll("[data-word]"), { yPercent: 115 }, { yPercent: 0, duration: 1, stagger: 0.055 })
      .fromTo(
        caseStudies.querySelector("[data-case-cta]"),
        { y: 20, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.8 },
        "-=0.7"
      )
      .fromTo(caseStudies.querySelector("[data-case-rule]"), { scaleX: 0 }, { scaleX: 1, duration: 1.1 }, "-=0.6");
    if (reduce) caseTl.progress(1);
    gsap.fromTo(
      caseStudies.querySelector("[data-case-tags]"),
      { xPercent: 4 },
      {
        xPercent: -34,
        ease: "none",
        scrollTrigger: { trigger: caseStudies, start: "top bottom", end: "bottom top", scrub: 0.5 },
      }
    );
  }

  /* ---------------- impact ---------------- */
  var impact = document.getElementById("impact");
  if (impact) {
    visible(impact);
    var impactTl = gsap.timeline({
      scrollTrigger: { trigger: impact, start: "top 82%" },
      defaults: { ease: "expo.out" },
    });
    impactTl
      .fromTo(impact.querySelectorAll("[data-stat]"), { y: 40, opacity: 0 }, { y: 0, opacity: 1, duration: 1, stagger: 0.14 })
      .fromTo(
        impact.querySelectorAll("[data-stat-rule]"),
        { scaleX: 0 },
        { scaleX: 1, duration: 1, stagger: 0.14 },
        "-=0.9"
      );
    if (reduce) impactTl.progress(1);
    qs(impact, "[data-stat-value]").forEach(function (el, i) {
      var target = Number(el.dataset.value || 0);
      var obj = { value: 0 };
      gsap.to(obj, {
        value: target,
        duration: 2.1,
        delay: 0.1 + 0.14 * i,
        ease: "power2.out",
        scrollTrigger: { trigger: impact, start: "top 82%" },
        onUpdate: function () {
          el.textContent = Math.round(obj.value).toLocaleString("en-US");
        },
      });
    });
  }

  /* ---------------- reviews ---------------- */
  var reviews = document.getElementById("reviews");
  if (reviews) {
    visible(reviews);
    var revTl = gsap.timeline({
      scrollTrigger: { trigger: reviews, start: "top 80%" },
      defaults: { ease: "expo.out" },
    });
    revTl
      .fromTo(reviews.querySelector("[data-reviews-label]"), { y: 22, opacity: 0 }, { y: 0, opacity: 1, duration: 0.8 })
      .fromTo(
        reviews.querySelectorAll("[data-review-card]"),
        { y: 56, opacity: 0 },
        { y: 0, opacity: 1, duration: 1, stagger: 0.09 },
        "-=0.55"
      );
    if (reduce) revTl.progress(1);
    var mm = gsap.matchMedia();
    mm.add("(min-width: 768px)", function () {
      gsap.fromTo(
        '[data-review-col="0"]',
        { y: 60 },
        {
          y: -70,
          ease: "none",
          scrollTrigger: { trigger: reviews, start: "top bottom", end: "bottom top", scrub: 0.55 },
        }
      );
      gsap.fromTo(
        '[data-review-col="1"]',
        { y: -20 },
        {
          y: 30,
          ease: "none",
          scrollTrigger: { trigger: reviews, start: "top bottom", end: "bottom top", scrub: 0.9 },
        }
      );
    });
  }

  /* ---------------- articles ---------------- */
  var articles = document.getElementById("articles");
  if (articles) {
    visible(articles);
    var artTl = gsap.timeline({
      scrollTrigger: { trigger: articles, start: "top 78%" },
      defaults: { ease: "expo.out" },
    });
    artTl
      .fromTo(articles.querySelectorAll("[data-word]"), { yPercent: 115 }, { yPercent: 0, duration: 1, stagger: 0.05 })
      .fromTo(
        articles.querySelector("[data-articles-cta]"),
        { y: 20, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.8 },
        "-=0.7"
      )
      .fromTo(
        articles.querySelectorAll("[data-article-card]"),
        { y: 60, opacity: 0 },
        { y: 0, opacity: 1, duration: 1, stagger: 0.11 },
        "-=0.55"
      )
      .fromTo(
        articles.querySelectorAll("[data-article-frame]"),
        { clipPath: "inset(0% 0% 100% 0%)" },
        { clipPath: "inset(0% 0% 0% 0%)", duration: 1.15, stagger: 0.11 },
        "-=1.0"
      )
      .fromTo(
        articles.querySelectorAll("[data-article-image]"),
        { scale: 1.2 },
        { scale: 1, duration: 1.5, stagger: 0.11 },
        "-=1.15"
      );
    if (reduce) artTl.progress(1);
    qs(articles, "[data-article-image]").forEach(function (img) {
      gsap.to(img, {
        yPercent: -7,
        ease: "none",
        scrollTrigger: { trigger: img, start: "top bottom", end: "bottom top", scrub: 0.7 },
      });
    });
  }

  /* ---------------- pricing ---------------- */
  var pricing = document.getElementById("pricing");
  if (pricing) {
    visible(pricing);
    var priceTl = gsap.timeline({
      scrollTrigger: { trigger: pricing, start: "top 76%" },
      defaults: { ease: "expo.out" },
    });
    priceTl
      .fromTo(pricing.querySelectorAll("[data-word]"), { yPercent: 115 }, { yPercent: 0, duration: 1.05, stagger: 0.05 })
      .fromTo(
        pricing.querySelector("[data-price-switch]"),
        { y: 20, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.8 },
        "-=0.7"
      )
      .fromTo(
        pricing.querySelectorAll("[data-plan-card]"),
        { y: 64, opacity: 0 },
        { y: 0, opacity: 1, duration: 1.05, stagger: 0.09 },
        "-=0.55"
      )
      .fromTo(
        pricing.querySelectorAll("[data-plan-feature]"),
        { x: -12, opacity: 0 },
        { x: 0, opacity: 1, duration: 0.5, stagger: 0.035 },
        "-=0.75"
      );
    if (reduce) priceTl.progress(1);

    var switchBtn = pricing.querySelector('[role="switch"]');
    var upfront = false;
    function applyPrices() {
      qs(pricing, "[data-price-value]").forEach(function (el) {
        var base = Number(el.dataset.base || 0);
        var target = upfront ? Math.round(base * 0.9) : base;
        var obj = { value: Number(String(el.textContent).replace(/\D/g, "")) || base };
        gsap.to(obj, {
          value: target,
          duration: 0.7,
          ease: "expo.out",
          onUpdate: function () {
            el.textContent = "$" + Math.round(obj.value);
          },
        });
      });
    }
    if (switchBtn) {
      switchBtn.addEventListener("click", function () {
        upfront = !upfront;
        switchBtn.setAttribute("aria-checked", upfront ? "true" : "false");
        var type = switchBtn.querySelector("span");
        var knob = type.querySelector("span");
        switchBtn.className =
          "flex items-center gap-2.5 rounded-full border py-1.5 pl-1.5 pr-4 text-[13px] transition-colors duration-500 [transition-timing-function:var(--ease-brand)] " +
          (upfront ? "border-ink bg-ink text-cream" : "border-ink/15 text-ink hover:border-ink/35");
        type.className =
          "relative h-5 w-9 rounded-full transition-colors duration-500 " + (upfront ? "bg-ember" : "bg-ink/12");
        knob.className =
          "absolute top-0.5 size-4 rounded-full bg-white transition-transform duration-500 [transition-timing-function:var(--ease-brand)] " +
          (upfront ? "translate-x-[18px]" : "translate-x-0.5");
        applyPrices();
      });
    }
  }

  /* ---------------- FAQ accordion ---------------- */
  var faq = document.getElementById("faq");
  var faqSections = qs(document, '[data-faq-row]').reduce(function (acc, row) {
    var sec = row.closest("section");
    if (sec && acc.indexOf(sec) === -1) acc.push(sec);
    return acc;
  }, []);

  faqSections.forEach(function (sec) {
    if (sec.querySelector("[data-faq-char]")) {
      visible(sec);
      var ftl = gsap.timeline({
        scrollTrigger: { trigger: sec, start: "top 76%" },
        defaults: { ease: "expo.out" },
      });
      ftl
        .fromTo(sec.querySelectorAll("[data-faq-eyebrow-char]"), { yPercent: 105 }, { yPercent: 0, duration: 0.7, stagger: 0.022 })
        .fromTo(
          sec.querySelectorAll("[data-faq-char]"),
          { yPercent: 108, opacity: 0 },
          { yPercent: 0, opacity: 1, duration: 0.85, stagger: 0.022 },
          "-=0.45"
        )
        .fromTo(
          sec.querySelector("[data-faq-frame]"),
          { clipPath: "inset(100% 0% 0% 0%)" },
          { clipPath: "inset(0% 0% 0% 0%)", duration: 1.2 },
          "-=0.8"
        )
        .fromTo(sec.querySelector("[data-faq-image]"), { scale: 1.22 }, { scale: 1, duration: 1.6 }, "-=1.2")
        .fromTo(
          sec.querySelectorAll("[data-faq-row]"),
          { y: 26, opacity: 0 },
          { y: 0, opacity: 1, duration: 0.7, stagger: 0.06 },
          "-=1.1"
        );
      if (reduce) ftl.progress(1);
      var fimg = sec.querySelector("[data-faq-image]");
      if (fimg) {
        gsap.to(fimg, {
          yPercent: -10,
          ease: "none",
          scrollTrigger: { trigger: sec.querySelector("[data-faq-frame]"), start: "top bottom", end: "bottom top", scrub: 0.6 },
        });
      }
    } else if (!sec.querySelector("#services-faq")) {
      visible(sec);
    }

    var rows = qs(sec, "[data-faq-row]");
    var openIndex = rows.findIndex(function (r) {
      return r.querySelector("button").getAttribute("aria-expanded") === "true";
    });
    if (openIndex === -1) openIndex = -1;

    function renderRow(row, open) {
      var btn = row.querySelector("button");
      var panel = row.querySelector('[role="region"]');
      var ans = panel.querySelector("p");
      var backdrop = row.querySelector('[aria-hidden="true"]');
      var qspan = btn.querySelector("span");
      var icon = btn.querySelectorAll("span > span")[1];
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      qspan.className =
        "flex-1 text-[16px] leading-[1.4] transition-colors duration-500 md:text-[18px] [transition-timing-function:var(--ease-brand)] " +
        (open ? "text-ink" : "text-ink/75 group-hover:text-ink");
      if (icon) {
        icon.className =
          "absolute h-px w-4 bg-ink transition-transform duration-500 [transition-timing-function:var(--ease-brand)] " +
          (open ? "rotate-0" : "rotate-90");
      }
      var tl = gsap.timeline();
      tl.to(panel, { height: open ? "auto" : 0, duration: 0.6, ease: "expo.out" })
        .to(backdrop, { scaleY: open ? 1 : 0, duration: 0.55, ease: "expo.out" }, 0)
        .to(ans, { y: open ? 0 : 14, opacity: open ? 1 : 0, duration: open ? 0.55 : 0.2, ease: "expo.out" }, open ? 0.12 : 0);
    }

    rows.forEach(function (row, i) {
      var open = i === openIndex;
      var panel = row.querySelector('[role="region"]');
      var ans = panel.querySelector("p");
      var backdrop = row.querySelector('[aria-hidden="true"]');
      gsap.set(panel, { height: open ? "auto" : 0 });
      gsap.set(ans, { y: open ? 0 : 14, opacity: open ? 1 : 0 });
      gsap.set(backdrop, { scaleY: open ? 1 : 0, transformOrigin: "top center" });
      row.querySelector("button").addEventListener("click", function () {
        if (openIndex === i) {
          openIndex = -1;
          renderRow(row, false);
        } else {
          var prev = openIndex;
          openIndex = i;
          if (prev > -1) renderRow(rows[prev], false);
          renderRow(row, true);
        }
      });
    });
  });

  /* ---------------- services page ---------------- */
  var servicesHero = document.getElementById("services-hero");
  if (servicesHero) {
    visible(servicesHero);
    var shTl = gsap.timeline({ defaults: { ease: "expo.out" } });
    shTl
      .fromTo(
        servicesHero.querySelectorAll("[data-hero-char]"),
        { yPercent: 105, opacity: 0 },
        { yPercent: 0, opacity: 1, duration: 0.7, stagger: 0.03 },
        0.15
      )
      .fromTo(
        servicesHero.querySelectorAll("[data-word]"),
        { yPercent: 118 },
        { yPercent: 0, duration: 1.2, stagger: 0.08 },
        0.3
      )
      .fromTo(servicesHero.querySelector("[data-hero-rule]"), { scaleX: 0 }, { scaleX: 1, duration: 1.4 }, 0.6);
    gsap.to(servicesHero.querySelector("[data-hero-block]"), {
      y: -60,
      opacity: 0.2,
      ease: "none",
      scrollTrigger: { trigger: servicesHero, start: "top top", end: "bottom top", scrub: 0.4 },
    });
  }

  var whereStart = document.getElementById("where-to-start");
  if (whereStart) {
    gsap.fromTo(
      whereStart.querySelector("[data-start-label]"),
      { x: -22, opacity: 0 },
      { x: 0, opacity: 1, duration: 0.9, ease: "expo.out", scrollTrigger: { trigger: whereStart, start: "top 82%" } }
    );
    gsap.to(whereStart.querySelectorAll("[data-start-word]"), {
      opacity: 1,
      ease: "none",
      stagger: 0.3,
      scrollTrigger: { trigger: whereStart.querySelector("[data-start-copy]"), start: "top 82%", end: "bottom 62%", scrub: 0.4 },
    });
  }

  var steps = document.getElementById("how-it-works");
  if (steps) {
    visible(steps);
    var stTl = gsap.timeline({
      scrollTrigger: { trigger: steps, start: "top 76%" },
      defaults: { ease: "expo.out" },
    });
    stTl
      .fromTo(steps.querySelector("[data-steps-panel]"), { y: 70, scale: 0.97 }, { y: 0, scale: 1, duration: 1.2 })
      .fromTo(steps.querySelectorAll("[data-word]"), { yPercent: 115 }, { yPercent: 0, duration: 1, stagger: 0.055 }, "-=0.85");
    gsap.fromTo(
      steps.querySelector("[data-steps-rule]"),
      { scaleX: 0 },
      {
        scaleX: 1,
        ease: "none",
        scrollTrigger: { trigger: steps.querySelector("[data-steps-grid]"), start: "top 72%", end: "bottom 72%", scrub: 0.5 },
      }
    );
    qs(steps, "[data-step]").forEach(function (step, i) {
      gsap.fromTo(
        step,
        { y: 46, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.9, ease: "expo.out", scrollTrigger: { trigger: step, start: "top 88%" } }
      );
      gsap.fromTo(
        step.querySelector("[data-step-number]"),
        { yPercent: 26 },
        {
          yPercent: -18,
          ease: "none",
          scrollTrigger: { trigger: step, start: "top bottom", end: "bottom top", scrub: 0.8 + 0.12 * i },
        }
      );
    });
  }

  /* ---------------- contact page ---------------- */
  var contactHero = document.getElementById("contact-hero");
  if (contactHero) {
    visible(contactHero);
    var cTl = gsap.timeline({ defaults: { ease: "expo.out" } });
    cTl
      .fromTo(
        contactHero.querySelectorAll("[data-contact-char]"),
        { yPercent: 105, opacity: 0 },
        { yPercent: 0, opacity: 1, duration: 0.7, stagger: 0.03 },
        0.15
      )
      .fromTo(contactHero.querySelectorAll("[data-word]"), { yPercent: 118 }, { yPercent: 0, duration: 1.15, stagger: 0.07 }, 0.3)
      .fromTo(contactHero.querySelectorAll("[data-value-marker]"), { scale: 0 }, { scale: 1, duration: 0.55, stagger: 0.11 }, 0.8)
      .fromTo(
        contactHero.querySelectorAll("[data-value-text]"),
        { x: -14, opacity: 0 },
        { x: 0, opacity: 1, duration: 0.7, stagger: 0.11 },
        0.86
      )
      .fromTo(contactHero.querySelector("[data-contact-quote]"), { y: 34, opacity: 0 }, { y: 0, opacity: 1, duration: 1 }, 1.1);
  }

  var channels = document.getElementById("channels");
  if (channels) {
    visible(channels);
    var chTl = gsap.timeline({
      scrollTrigger: { trigger: channels, start: "top 78%" },
      defaults: { ease: "expo.out" },
    });
    chTl
      .fromTo(channels.querySelector("[data-channel-label]"), { y: 20, opacity: 0 }, { y: 0, opacity: 1, duration: 0.75 })
      .fromTo(channels.querySelectorAll("[data-word]"), { yPercent: 115 }, { yPercent: 0, duration: 1, stagger: 0.05 }, "-=0.5");
    if (reduce) chTl.progress(1);
    qs(channels, "[data-channel]").forEach(function (ch, i) {
      var tl = gsap.timeline({
        scrollTrigger: { trigger: ch, start: "top 88%" },
        defaults: { ease: "expo.out" },
        delay: 0.05 * i,
      });
      tl.fromTo(ch.querySelector("[data-channel-rule]"), { scaleX: 0 }, { scaleX: 1, duration: 0.9 }).fromTo(
        ch.querySelectorAll("[data-channel-part]"),
        { y: 26, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.75, stagger: 0.07 },
        "-=0.65"
      );
    });
  }

  var helpful = document.getElementById("helpful");
  if (helpful) {
    visible(helpful);
    gsap.fromTo(
      helpful.querySelector("[data-helpful-label]"),
      { y: 18, opacity: 0 },
      {
        y: 0,
        opacity: 1,
        duration: 0.8,
        ease: "expo.out",
        scrollTrigger: { trigger: helpful, start: "top 80%" },
      }
    );
    gsap.fromTo(
      helpful.querySelectorAll("[data-note]"),
      { y: 30, opacity: 0 },
      {
        y: 0,
        opacity: 1,
        duration: 0.8,
        stagger: 0.08,
        ease: "expo.out",
        scrollTrigger: { trigger: helpful, start: "top 78%" },
      }
    );
  }

  /* contact enquiry form */
  var formCard = document.querySelector("[data-form-card]");
  if (formCard) {
    visible(formCard);
    var formTl = gsap.timeline({
      scrollTrigger: { trigger: formCard, start: "top 82%" },
      defaults: { ease: "expo.out" },
    });
    formTl
      .fromTo(formCard, { y: 60, opacity: 0 }, { y: 0, opacity: 1, duration: 1.1 })
      .fromTo(
        formCard.querySelectorAll("[data-form-field]"),
        { y: 24, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.7, stagger: 0.07 },
        "-=0.75"
      );
    if (reduce) formTl.progress(1);

    var form = formCard.querySelector("form");
    var originalHTML = formCard.innerHTML;
    var contents = Array.prototype.slice.call(formCard.children);

    function fieldError(field, msg) {
      var wrap = field.closest("[data-form-field]") || field.parentElement;
      field.classList.add("border-red-300/70");
      field.classList.remove("border-white/15");
      var p = document.createElement("p");
      p.className = "mt-1.5 text-[12px] text-red-300";
      p.textContent = msg;
      wrap.appendChild(p);
    }

    function clearErrors() {
      formCard.querySelectorAll(".text-red-300").forEach(function (el) {
        if (el.tagName === "P" && el.parentElement) el.parentElement.removeChild(el);
      });
      formCard.querySelectorAll(".border-red-300\\/70").forEach(function (el) {
        el.classList.remove("border-red-300/70");
        el.classList.add("border-white/15");
      });
    }

    function bindForm() {
      form = formCard.querySelector("form");
      if (!form) return;
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        clearErrors();
        var data = new FormData(form);
        var name = String(data.get("name") || "").trim();
        var email = String(data.get("email") || "").trim();
        var role = String(data.get("role") || "");
        var company = String(data.get("company") || "").trim();
        var topic = String(data.get("topic") || "");
        var ok = true;
        var errs = {
          name: form.querySelector("#name"),
          email: form.querySelector("#email"),
          role: form.querySelector("#role"),
          company: form.querySelector("#company"),
          topic: form.querySelector("#topic"),
        };
        if (name.length < 2) {
          fieldError(errs.name, "Please tell us your name.");
          ok = false;
        }
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
          fieldError(errs.email, "Please enter a valid email address.");
          ok = false;
        }
        if (!role) {
          fieldError(errs.role, "Please choose the closest role.");
          ok = false;
        }
        if (company.length < 2) {
          fieldError(errs.company, "Please tell us where you work.");
          ok = false;
        }
        if (!topic) {
          fieldError(errs.topic, "Please choose what you would like help with.");
          ok = false;
        }
        if (!ok) return;

        var btn = form.querySelector('button[type="submit"]');
        var label = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Sending…";
        setTimeout(function () {
          contents.forEach(function (el) {
            el.style.display = "none";
          });
          var done = document.createElement("div");
          done.setAttribute("data-form-done", "");
          done.className = "py-10 text-center";
          done.innerHTML =
            '<span class="mx-auto grid size-14 place-items-center rounded-full bg-ember text-ink"><svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-check size-6"><path d="M20 6 9 17l-5-5"></path></svg></span>' +
            '<h3 class="font-display mt-6 text-[24px] leading-[1.15] text-white md:text-[28px]">Thank you — your enquiry is with us.</h3>' +
            '<p class="mx-auto mt-3 max-w-[40ch] text-[15px] leading-[1.6] text-line">A member of the care team reads every enquiry personally. You will hear back within one working day.</p>' +
            '<button type="button" class="mt-8 text-[14px] text-ember underline underline-offset-4 transition-opacity duration-300 hover:opacity-70">Send another enquiry</button>';
          formCard.appendChild(done);
          gsap.fromTo(
            done,
            { clipPath: "inset(0% 0% 100% 0%)", opacity: 0 },
            { clipPath: "inset(0% 0% 0% 0%)", opacity: 1, duration: 0.8, ease: "expo.out" }
          );
          done.querySelector("button").addEventListener("click", function () {
            done.remove();
            contents.forEach(function (el) {
              el.style.display = "";
            });
            var b = formCard.querySelector('button[type="submit"]');
            if (b) {
              b.disabled = false;
              b.textContent = label;
            }
          });
        }, 650);
      });
    }
    if (form) bindForm();
  }

  /* ---------------- final CTA ---------------- */
  var ctaCard = document.querySelector("[data-cta-card]");
  if (ctaCard) {
    var ctaSection = ctaCard.closest("section");
    visible(ctaSection);
    var ctaTl = gsap.timeline({
      scrollTrigger: { trigger: ctaSection, start: "top 78%" },
      defaults: { ease: "expo.out" },
    });
    ctaTl
      .fromTo(ctaCard, { y: 72, scale: 0.97 }, { y: 0, scale: 1, duration: 1.2 })
      .fromTo(
        ctaSection.querySelectorAll("[data-word]"),
        { yPercent: 110 },
        { yPercent: 0, duration: 1, stagger: 0.045 },
        "-=0.85"
      )
      .fromTo(
        ctaSection.querySelectorAll("[data-cta-body]"),
        { y: 22, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.8 },
        "-=0.6"
      )
      .fromTo(
        ctaSection.querySelectorAll("[data-cta-action]"),
        { y: 18, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.7, stagger: 0.08 },
        "-=0.5"
      );
    if (reduce) ctaTl.progress(1);
    var portrait = ctaSection.querySelector("[data-cta-portrait]");
    if (portrait) {
      gsap.fromTo(
        portrait,
        { y: 40 },
        {
          y: 0,
          ease: "none",
          scrollTrigger: { trigger: ctaSection, start: "top bottom", end: "bottom bottom", scrub: 0.6 },
        }
      );
    }
  }

  /* ---------------- footer ---------------- */
  var footer = document.querySelector("footer");
  if (footer) {
    visible(footer);
    var footTl = gsap.timeline({
      scrollTrigger: { trigger: footer, start: "top 85%" },
      defaults: { ease: "expo.out" },
    });
    footTl.fromTo(
      footer.querySelectorAll("[data-foot-col]"),
      { y: 34, opacity: 0 },
      { y: 0, opacity: 1, duration: 0.9, stagger: 0.09 }
    );
    if (reduce) footTl.progress(1);
    if (footer.querySelector("[data-wordmark]")) {
      gsap.fromTo(
        footer.querySelector("[data-wordmark]"),
        { yPercent: 55 },
        {
          yPercent: 0,
          ease: "none",
          scrollTrigger: { trigger: footer.querySelector("[data-wordmark-mask]"), start: "top bottom", end: "bottom bottom", scrub: 0.5 },
        }
      );
    }

    var news = footer.querySelector("form");
    if (news) {
      news.addEventListener("submit", function (e) {
        e.preventDefault();
        var input = news.querySelector('input[type="email"]');
        var btn = news.querySelector('button[type="submit"]');
        var msg = news.querySelector('[aria-live="polite"]');
        if (!input || !input.value.trim()) return;
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(input.value.trim())) {
          input.focus();
          return;
        }
        btn.innerHTML =
          '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-check size-4"><path d="M20 6 9 17l-5-5"></path></svg>';
        if (msg) msg.textContent = "Thanks — you're on the list.";
        input.value = "";
      });
    }
  }

  /* ---------------- smooth anchors ---------------- */
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest('a[href^="#"]') : null;
    if (!a) return;
    var href = a.getAttribute("href");
    if (!href || href === "#" || href.length < 2) return;
    var target = document.querySelector(href);
    if (!target) return;
    e.preventDefault();
    scrollToEl(target, -100);
  });

  /* ---------------- refresh ---------------- */
  var refreshTimer = null;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function () {
      ScrollTrigger.refresh();
    }, 160);
  }
  document.addEventListener(
    "load",
    function (e) {
      var tag = e.target && e.target.tagName;
      if (tag === "IMG" || tag === "VIDEO") scheduleRefresh();
    },
    true
  );
  window.addEventListener("load", function () {
    ScrollTrigger.refresh();
  });
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () {
      ScrollTrigger.refresh();
    });
  }
})();
