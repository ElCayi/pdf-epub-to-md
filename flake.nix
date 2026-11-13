{
  description = "pdf-epub-to-md: novelas de PDF y EPUB a Markdown para Obsidian";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { nixpkgs, ... }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python3.withPackages (pythonPackages: [
            pythonPackages.pymupdf
          ]);
        in
        {
          default = pkgs.mkShell {
            packages = [ python ];

            shellHook = ''
              echo "Entorno listo: $(python --version) · PyMuPDF $(python -c 'import fitz; print(fitz.__version__)')"
            '';
          };
        }
      );
    };
}
