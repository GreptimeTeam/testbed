{
  description = "Development environment flake";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
  };
  inputs.self.submodules = true;

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        buildInputs = with pkgs; [
          libgit2
          libz
          stdenv.cc.cc.lib
        ];
        lib = nixpkgs.lib;
      in
      {
        devShells.default = pkgs.mkShell {
          nativeBuildInputs = with pkgs; [
            pkg-config
            git
            curl
            kind
            podman
            garage
            postgresql.out
            process-compose
            etcd
            haproxy
          ];

          buildInputs = buildInputs;
          NIX_HARDENING_ENABLE = "";
          PC_PORT_NUM = "11099";
          LD_LIBRARY_PATH = lib.makeLibraryPath buildInputs;
        };
      });
}
