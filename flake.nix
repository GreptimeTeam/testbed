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
        pythonEnv = pkgs.python313.withPackages (ps: [
          ps.pyiceberg
          ps.pyarrow
          ps.boto3
          ps.fastavro
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          nativeBuildInputs = with pkgs; [
            pkg-config
            git
            curl
            kind
            podman
            podman-compose
            garage
            postgresql.out
            mariadb
            process-compose
            etcd
            haproxy
            awscli2
            duckdb
            pythonEnv
          ];

          buildInputs = buildInputs;
          NIX_HARDENING_ENABLE = "";
          PC_PORT_NUM = "11099";
          LD_LIBRARY_PATH = lib.makeLibraryPath buildInputs;

          shellHook = ''
            # Source Garage S3 credentials if available
            if [ -f .greptimedb/s3.env ]; then
              . .greptimedb/s3.env
            fi
          '';
        };
      });
}
