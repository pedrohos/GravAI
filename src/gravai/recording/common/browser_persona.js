// Makes this browser look like Chrome on a Windows desktop, to whatever runs
// inside the page.
//
// The headers a persona rewrites are handled over CDP (user agent, client hints,
// timezone, locale); this file covers what only a script can see - the platform
// strings, the plugin list, the screen, the GPU, the installed voices and fonts.
// Values come from {{PERSONA}}, substituted by browser_persona.py.
//
// Two constraints this file has to respect, both learned the hard way:
//
//   * It is injected at document start into *every* frame, including the
//     opaque-origin iframes Meet creates while loading. Registering a blob URL
//     in one of those kills the renderer (see rtc_intercept.js), so nothing here
//     touches URL.createObjectURL, and every step is wrapped so a frame that
//     lacks an API skips it instead of throwing.
//   * A patch that is visible as a patch is worse than no patch. Anything
//     replaced here reports itself as native code when stringified.
(() => {
  "use strict";

  const P = {{PERSONA}};

  const safe = (fn) => {
    try {
      fn();
    } catch (_) {
      // A frame without this API, or one that froze it. Nothing to do.
    }
  };

  // --- keeping the patches from announcing themselves ---------------------
  //
  // Function.prototype.toString on a replaced getter would print its source,
  // which is a plainer tell than the value it hides. Every function installed
  // below is registered here and stringifies as native code instead.
  const nativeNames = new WeakMap();
  const nativeToString = Function.prototype.toString;

  const cloakedToString = new Proxy(nativeToString, {
    apply(target, thisArg, args) {
      const name = nativeNames.get(thisArg);
      if (name !== undefined) {
        return "function " + name + "() { [native code] }";
      }
      return Reflect.apply(target, thisArg, args);
    },
  });

  safe(() => {
    Function.prototype.toString = cloakedToString;
    nativeNames.set(cloakedToString, "toString");
    nativeNames.set(nativeToString, "toString");
  });

  const asNative = (fn, name) => {
    nativeNames.set(fn, name);
    return fn;
  };

  /** Replaces an accessor, leaving it configurable and native-looking. */
  const defineGetter = (target, prop, value) => {
    safe(() => {
      const getter = asNative(() => value, "get " + prop);
      Object.defineProperty(target, prop, {
        get: getter,
        set: undefined,
        configurable: true,
        enumerable: true,
      });
    });
  };

  /** Replaces a method, keeping the name Chrome would report for it. */
  const defineMethod = (target, prop, impl) => {
    safe(() => {
      const label = typeof prop === "string" ? prop : "[" + String(prop) + "]";
      Object.defineProperty(impl, "name", { value: label, configurable: true });
      asNative(impl, label);
      Object.defineProperty(target, prop, {
        value: impl,
        writable: true,
        configurable: true,
        enumerable: false,
      });
    });
  };

  // Properties live on the prototype in Chrome, not on the instance. Patching
  // the instance leaves the prototype's own descriptor visible one step up the
  // chain, which is exactly the sort of inconsistency that gets looked for.
  const navProto = Object.getPrototypeOf(navigator) || Navigator.prototype;
  const screenProto = Object.getPrototypeOf(screen) || Screen.prototype;

  // --- navigator ----------------------------------------------------------

  // --disable-blink-features=AutomationControlled already reports false here.
  // Only patch when it did not take, and to false rather than undefined: a real
  // Chrome has the property.
  if (navigator.webdriver !== false) {
    defineGetter(navProto, "webdriver", false);
  }

  defineGetter(navProto, "platform", P.platform);
  defineGetter(navProto, "vendor", P.vendor);
  defineGetter(navProto, "languages", Object.freeze(P.languages.slice()));
  defineGetter(navProto, "hardwareConcurrency", P.hardwareConcurrency);
  defineGetter(navProto, "deviceMemory", P.deviceMemory);
  defineGetter(navProto, "maxTouchPoints", P.maxTouchPoints);
  defineGetter(navProto, "pdfViewerEnabled", true);

  // A desktop Chrome reports a connection; a container that never had one is
  // conspicuous by the missing object.
  safe(() => {
    if (!navigator.connection && window.NetworkInformation) {
      const connection = Object.create(NetworkInformation.prototype);
      defineGetter(connection, "downlink", P.connection.downlink);
      defineGetter(connection, "effectiveType", P.connection.effectiveType);
      defineGetter(connection, "rtt", P.connection.rtt);
      defineGetter(connection, "saveData", false);
      defineGetter(navProto, "connection", connection);
    }
  });

  // --- plugins and mime types ---------------------------------------------
  //
  // Headless reports none. The five below are what Chrome ships on the desktop:
  // all five are the same built-in PDF viewer under different names, and they
  // are cross-linked with the two mime types, since a plugin list whose entries
  // do not resolve back through namedItem is its own tell.
  safe(() => {
    if (!window.Plugin || !window.PluginArray || navigator.plugins.length > 0) {
      return;
    }

    const mimeTypes = P.mimeTypes.map((spec) => {
      const mime = Object.create(MimeType.prototype);
      defineGetter(mime, "type", spec.type);
      defineGetter(mime, "suffixes", spec.suffixes);
      defineGetter(mime, "description", spec.description);
      return mime;
    });

    const plugins = P.plugins.map((spec) => {
      const plugin = Object.create(Plugin.prototype);
      defineGetter(plugin, "name", spec.name);
      defineGetter(plugin, "filename", spec.filename);
      defineGetter(plugin, "description", spec.description);
      defineGetter(plugin, "length", mimeTypes.length);
      mimeTypes.forEach((mime, index) => {
        defineGetter(plugin, String(index), mime);
        defineGetter(plugin, mime.type, mime);
      });
      defineMethod(plugin, "item", (index) => mimeTypes[index] || null);
      defineMethod(
        plugin,
        "namedItem",
        (name) => mimeTypes.find((mime) => mime.type === name) || null,
      );
      return plugin;
    });

    // Each mime type points back at the plugin that handles it.
    mimeTypes.forEach((mime) => defineGetter(mime, "enabledPlugin", plugins[0]));

    const collection = (proto, items, keyOf) => {
      const list = Object.create(proto);
      items.forEach((item, index) => {
        defineGetter(list, String(index), item);
        defineGetter(list, keyOf(item), item);
      });
      defineGetter(list, "length", items.length);
      defineMethod(list, "item", (index) => items[index] || null);
      defineMethod(
        list,
        "namedItem",
        (name) => items.find((item) => keyOf(item) === name) || null,
      );
      defineMethod(list, "refresh", () => undefined);
      defineMethod(list, Symbol.iterator, function* () {
        yield* items;
      });
      return list;
    };

    defineGetter(
      navProto,
      "plugins",
      collection(PluginArray.prototype, plugins, (plugin) => plugin.name),
    );
    defineGetter(
      navProto,
      "mimeTypes",
      collection(MimeTypeArray.prototype, mimeTypes, (mime) => mime.type),
    );
  });

  // --- screen and window geometry -----------------------------------------
  //
  // The browser is launched at the persona's size, so the inner dimensions are
  // already right; these are the desktop around it, which the container has none
  // of. availHeight is short by the height of the Windows taskbar.
  defineGetter(screenProto, "width", P.screen.width);
  defineGetter(screenProto, "height", P.screen.height);
  defineGetter(screenProto, "availWidth", P.screen.availWidth);
  defineGetter(screenProto, "availHeight", P.screen.availHeight);
  defineGetter(screenProto, "availLeft", 0);
  defineGetter(screenProto, "availTop", 0);
  defineGetter(screenProto, "colorDepth", P.screen.colorDepth);
  defineGetter(screenProto, "pixelDepth", P.screen.colorDepth);

  safe(() => {
    if (window.top === window.self) {
      // Only in the top frame: an iframe reports the values of the window that
      // holds it, and rewriting them there contradicts its own geometry.
      defineGetter(window, "outerWidth", P.screen.width);
      defineGetter(window, "outerHeight", P.screen.availHeight);
      defineGetter(window, "screenX", 0);
      defineGetter(window, "screenY", 0);
      defineGetter(window, "screenLeft", 0);
      defineGetter(window, "screenTop", 0);
    }
    defineGetter(window, "devicePixelRatio", P.devicePixelRatio);
  });

  // --- the GPU ------------------------------------------------------------
  //
  // The honest answer here is SwiftShader: this LXC has no /dev/dri, so every
  // pixel is drawn on the CPU. What follows reports the workstation's card
  // instead - the strings and the limits that go with a real D3D11 driver.
  safe(() => {
    // Omitted deliberately when the persona would rather answer honestly: a
    // claimed card whose pixels are drawn by SwiftShader is a claim that can be
    // checked by rendering, and a caught claim is worse than a plain answer.
    if (!P.webgl) return;
    const spoofed = {
      37445: P.webgl.unmaskedVendor, // UNMASKED_VENDOR_WEBGL
      37446: P.webgl.unmaskedRenderer, // UNMASKED_RENDERER_WEBGL
      3379: P.webgl.maxTextureSize, // MAX_TEXTURE_SIZE
      34024: P.webgl.maxTextureSize, // MAX_RENDERBUFFER_SIZE
      34076: P.webgl.maxTextureSize, // MAX_CUBE_MAP_TEXTURE_SIZE
    };

    for (const proto of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
      if (!proto) continue;
      const original = proto.prototype.getParameter;
      defineMethod(proto.prototype, "getParameter", function getParameter(parameter) {
        if (parameter === 3386) {
          // MAX_VIEWPORT_DIMS, which is a typed array rather than a number.
          return new Int32Array(P.webgl.maxViewportDims);
        }
        if (parameter in spoofed) {
          return spoofed[parameter];
        }
        return original.call(this, parameter);
      });
    }
  });

  // WebGPU reports the adapter by name on a real desktop, and either nothing or
  // a software fallback here.
  safe(() => {
    if (!P.webgpu || !navigator.gpu || !window.GPUAdapter) return;
    const info = {
      vendor: P.webgpu.vendor,
      architecture: P.webgpu.architecture,
      device: "",
      description: P.webgpu.description,
    };
    if ("info" in GPUAdapter.prototype) {
      defineGetter(GPUAdapter.prototype, "info", info);
    }
    if (GPUAdapter.prototype.requestAdapterInfo) {
      defineMethod(GPUAdapter.prototype, "requestAdapterInfo", function requestAdapterInfo() {
        return Promise.resolve(info);
      });
    }
  });

  // --- window.chrome ------------------------------------------------------
  //
  // Real Chrome ships this; Chromium and headless leave holes in it. Only the
  // missing pieces are filled, so a genuine object is left alone.
  safe(() => {
    if (!window.chrome) {
      Object.defineProperty(window, "chrome", { value: {}, writable: true, configurable: true });
    }
    const chrome = window.chrome;
    if (!chrome.app) {
      chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" },
        RunningState: { CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running" },
        getDetails: asNative(function getDetails() { return null; }, "getDetails"),
        getIsInstalled: asNative(function getIsInstalled() { return false; }, "getIsInstalled"),
        runningState: asNative(function runningState() { return "cannot_run"; }, "runningState"),
      };
    }
    if (!chrome.csi) {
      chrome.csi = asNative(function csi() {
        const timing = performance.timing || {};
        const start = timing.navigationStart || Date.now();
        return {
          startE: start,
          onloadT: timing.domContentLoadedEventEnd || start,
          pageT: performance.now(),
          tran: 15,
        };
      }, "csi");
    }
    if (!chrome.loadTimes) {
      chrome.loadTimes = asNative(function loadTimes() {
        const nav = performance.getEntriesByType("navigation")[0] || {};
        const start = (performance.timeOrigin || Date.now()) / 1000;
        return {
          requestTime: start,
          startLoadTime: start,
          commitLoadTime: start + (nav.responseStart || 0) / 1000,
          finishDocumentLoadTime: start + (nav.domContentLoadedEventEnd || 0) / 1000,
          finishLoadTime: start + (nav.loadEventEnd || 0) / 1000,
          firstPaintTime: start + (nav.responseEnd || 0) / 1000,
          firstPaintAfterLoadTime: 0,
          navigationType: "Other",
          wasFetchedViaSpdy: true,
          wasNpnNegotiated: true,
          npnNegotiatedProtocol: "h3",
          wasAlternateProtocolAvailable: false,
          connectionInfo: "h3",
        };
      }, "loadTimes");
    }
  });

  // --- permissions --------------------------------------------------------
  //
  // Headless answers 'prompt' for notifications while Notification.permission
  // says 'denied'. A desktop Chrome never disagrees with itself here.
  safe(() => {
    const permissions = navigator.permissions;
    if (!permissions || !permissions.query) return;
    const original = permissions.query;
    defineMethod(permissions, "query", function query(parameters) {
      if (parameters && parameters.name === "notifications") {
        return Promise.resolve({
          state: Notification.permission === "denied" ? "prompt" : Notification.permission,
          onchange: null,
        });
      }
      return original.call(permissions, parameters);
    });
  });

  safe(() => {
    if (window.Notification && Notification.permission === "denied") {
      defineGetter(Notification, "permission", "default");
    }
  });

  // --- installed voices ---------------------------------------------------
  //
  // A Windows desktop has the Microsoft voices; this container has no speech
  // engine at all, so getVoices returns an empty list - a cheap thing to check
  // and a definite one when it comes back empty.
  safe(() => {
    if (!window.speechSynthesis || speechSynthesis.getVoices().length > 0) return;
    const voices = P.voices.map((spec) => ({
      voiceURI: spec.name,
      name: spec.name,
      lang: spec.lang,
      localService: spec.localService,
      default: spec.default === true,
    }));
    defineMethod(speechSynthesis, "getVoices", function getVoices() {
      return voices.slice();
    });
  });

  // --- fonts --------------------------------------------------------------
  //
  // Only answers the question 'is this font installed'. Text measured on a
  // canvas is still drawn with whatever fonts this machine actually has, which
  // no script can change - see the note in browser_persona.py.
  safe(() => {
    if (!document.fonts || !document.fonts.check) return;
    const installed = new Set(P.fonts.map((font) => font.toLowerCase()));
    const original = document.fonts.check.bind(document.fonts);
    defineMethod(document.fonts, "check", function check(font, text) {
      const family = String(font)
        .replace(/^.*?\d+(\.\d+)?(px|pt|em|rem|%)\s+/, "")
        .replace(/['"]/g, "")
        .split(",")[0]
        .trim()
        .toLowerCase();
      if (installed.has(family)) {
        return true;
      }
      return original(font, text);
    });
  });
})();
