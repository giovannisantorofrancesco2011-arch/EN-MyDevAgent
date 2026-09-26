// Vio, MyDevAgent's little purple octopus: the same 14×8 pixel art as in the terminal (mydevagent/tui/mascot.py),
// drawn in SVG. It changes expression with the mode and moves its tentacles while working.
(function () {
  const COLORS = {
    P: "#a855f7", // body
    D: "#581c87", // outline and tentacles
    L: "#d8b4fe", // highlight
    K: "#1a0b2e", // eyes and mouth
    W: "#ffffff", // light in the eyes
    C: "#f472b6", // cheeks and hearts
    Y: "#facc15", // star eyes
    B: "#67e8f9", // glasses
    R: "#f43f5e", // error
  };
  const HEAD = [".....DDDD.....", "...DDPPPPDD...", "..DPLPPPPPPD.."];
  const EYES = {
    open: [".DPPWKPPWKPPD.", ".DPPKKPPKKPPD."],
    happy: [".DPPKPPPPKPPD.", ".DPKPKPPKPKPD."],
    closed: [".DPPPPPPPPPPD.", ".DPKKKPPKKKPD."],
    look: [".DPPPWKPPWKPD.", ".DPPPKKPPKKPD."],
    glasses: [".DPBBBPPBBBPD.", ".DPBWKBBWKBPD."],
    stars: [".DPPYWPPYWPPD.", ".DPPYYPPYYPPD."],
    cross: [".DPRPRPPRPRPD.", ".DPPRPPPPRPPD."],
    hearts: [".DPCPCPPCPCPD.", ".DPPCPPPPCPPD."],
  };
  const MOUTHS = { smile: ".DPCPPKKPPCPD.", open: ".DPCPKKKKPCPD.", flat: ".DPCPPDDPPCPD." };
  const TENTACLES = [
    ["DPDPDPPPPDPDPD", "D.P.P.DD.P.P.D"],
    ["DPDPDPPPPDPDPD", ".D.P.PDDP.P.D."],
  ];
  const EXPRESSIONS = {
    ask: ["open", "smile"],
    "auto-edit": ["happy", "open"],
    plan: ["glasses", "flat"],
    auto: ["stars", "open"],
    chat: ["look", "smile"],
    think: ["look", "flat"],
    look: ["open", "flat"],
    blink: ["closed", "smile"],
    done: ["happy", "smile"],
    error: ["cross", "flat"],
    love: ["hearts", "smile"],
  };
  const SAYS = {
    ask: "I ask you before every change.",
    "auto-edit": "I edit files by myself, and ask you before commands.",
    plan: "I read and propose a plan, without touching anything.",
    auto: "I do everything by myself, inside this folder.",
  };
  const PATS = ["Thanks! ♥", "Octopus purring in progress… ♥", "Eight tentacles ready to code! ♥", "More, more! ♥"];

  function sprite(expression, frame) {
    const [eyes, mouth] = EXPRESSIONS[expression] || EXPRESSIONS.ask;
    return HEAD.concat(EYES[eyes], [MOUTHS[mouth]], TENTACLES[frame % TENTACLES.length]);
  }

  /** Vio as SVG: `size` is the side of one pixel. */
  function svg(expression, frame, size) {
    const rows = sprite(expression, frame || 0);
    const px = size || 4;
    let rects = "";
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const color = COLORS[row[x]];
        if (color) rects += `<rect x="${x}" y="${y}" width="1.02" height="1.02" fill="${color}"/>`;
      }
    });
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 8" width="${14 * px}" height="${8 * px}" ` +
      `shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
  }

  window.Vio = { svg, SAYS, PATS, EXPRESSIONS };
})();
